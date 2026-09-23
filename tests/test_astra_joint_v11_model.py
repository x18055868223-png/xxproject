import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_v11_model as model
import astra_joint_v11_inference as inference
from astra_joint_v11_inference import load_artifact, predict_row, transform_row


def ms(dt):
    return int(dt.timestamp() * 1000)


def small_protocol(**overrides):
    protocol = dict(model.DEFAULT_PROTOCOL)
    protocol.update(
        {
            "rolling_development_start": "2020-04-01",
            "rolling_development_end": "2020-06-30",
            "rolling_step_months": 3,
            "rolling_horizon_months": 3,
            "final_window_months": 3,
            "calibration_months": 1,
            "final_24m_end": "2020-07-31",
            "min_delivery_days": 20,
            "min_positive_delivery_days": 5,
            "min_tail_delivery_days_for_precise_probability": 99,
            "logistic_C": [0.1, 1.0],
            "gamma_alpha": [0.1],
        }
    )
    protocol.update(overrides)
    return protocol


def test_contract_rolling_validation_uses_four_annual_folds():
    _groups, protocol, status = model.load_contract()

    assert status["status"] == "loaded"
    assert protocol["rolling_step_months"] == 12
    assert protocol["rolling_horizon_months"] == 12
    windows = model.rolling_windows(protocol)
    assert [window["name"] for window in windows] == [
        "2022-01-01__2023-01-01",
        "2023-01-01__2024-01-01",
        "2024-01-01__2025-01-01",
        "2025-01-01__2026-01-01",
    ]
    assert windows[0]["train_start"].date().isoformat() == "2020-01-01"
    assert windows[0]["cal_start"].date().isoformat() == "2021-10-01"


def synthetic_rows(days=220):
    rows = []
    start = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    for day_index in range(days):
        day = start + timedelta(days=day_index)
        for hour in (0, 12):
            as_of = day + timedelta(hours=hour)
            expiry = as_of + timedelta(hours=12)
            for side in ("put_credit", "call_credit"):
                side_sign = -1.0 if side == "put_credit" else 1.0
                base = math.sin(day_index / 9) * 0.01 + (0.004 if side == "put_credit" else -0.002)
                adverse = max(0.0, base * side_sign)
                # Include many zeros, a positive body, and a few large losses.
                loss = 0.0
                if (day_index + (0 if side == "put_credit" else 3)) % 5 == 0:
                    loss = 0.08 + adverse * 4
                if side == "put_credit" and day_index in (30, 95, 160):
                    loss = 1.35
                rows.append(
                    {
                        "row_id": f"{day_index}-{hour}-{side}",
                        "observation_id": f"{day_index}-{hour}",
                        "as_of_ms": ms(as_of),
                        "entry_ms": ms(as_of + timedelta(minutes=1)),
                        "expiry_ms": ms(expiry),
                        "delivery_date": expiry.strftime("%Y-%m-%d"),
                        "side": side,
                        "target_width": "2000",
                        "actual_width": "2000",
                        "entry_price": str(50000 + day_index * 20),
                        "loss_normalized": str(loss),
                        "payout_btc": str(loss * 2000 / (50000 + day_index * 20)),
                        "protection_breached": "1" if loss > 1 else "0",
                        "dte_hours": "12",
                        "short_distance_fraction": str(0.02 + (day_index % 7) * 0.001),
                        "width_fraction": "0.04",
                        "side_sign": str(side_sign),
                        "hour_sin": str(math.sin(hour / 24 * math.tau)),
                        "hour_cos": str(math.cos(hour / 24 * math.tau)),
                        "ret_15": str(base),
                        "ret_30": str(base * 1.5),
                        "ret_240": str(base * 4),
                        "ret_720": str(base * 6),
                        "ret_1440": str(base * 8),
                        "vol_15": str(abs(base) + 0.001),
                        "vol_30": str(abs(base) + 0.002),
                        "vol_240": str(abs(base) + 0.004),
                        "net_flow_15": str(base * 10),
                        "net_flow_30": str(base * 20),
                        "net_flow_240": str(base * 35),
                        "side_adverse_ret_15": str(adverse),
                        "side_adverse_ret_30": str(adverse * 1.5),
                        "side_adverse_flow_30": str(adverse * 10),
                        "range_position_15": str((day_index % 10) / 10),
                        "range_position_30": str((day_index % 12) / 12),
                        "vwap_deviation_15": str(base / 2),
                        "vwap_deviation_30": str(base / 3),
                        "pressure_response_15": str(base * abs(base)),
                        "pressure_response_30": str(base * abs(base) * 2),
                        "side_pressure_response_15": str(adverse * 0.5),
                        "side_pressure_response_30": str(adverse),
                    }
                )
    return rows


def write_csv(tmp_path, rows):
    path = tmp_path / "v11_common_fixed_clock.csv"
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_rejects_event_only_features_from_nr_main_model():
    with pytest.raises(ValueError, match="event-only"):
        model.validate_groups({"geometry": ["dte_hours"], "statistical": ["shock_magnitude"], "joint": ["dte_hours"]})


def test_selection_uses_mse_before_mae():
    summaries = [
        {
            "candidate_id": "low-mae-high-mse",
            "model_family": "gam",
            "feature_group": "geometry",
            "exportable_stdlib": True,
            "status": "available",
            "hyperparameters": {"logistic_C": 0.1, "gamma_alpha": 0.1},
            "rolling_metrics": {"expected_loss_mae": 0.01, "expected_loss_mse": 0.30, "expected_loss_mse_se": 0.0},
        },
        {
            "candidate_id": "higher-mae-low-mse",
            "model_family": "gam",
            "feature_group": "statistical",
            "exportable_stdlib": True,
            "status": "available",
            "hyperparameters": {"logistic_C": 0.1, "gamma_alpha": 0.1},
            "rolling_metrics": {"expected_loss_mae": 0.20, "expected_loss_mse": 0.05, "expected_loss_mse_se": 0.0},
        },
    ]
    selected = model.select_candidate(summaries, {"geometry": ["a"], "statistical": ["a", "b"], "joint": ["a", "b", "c"]})
    assert selected["selected_candidate_id"] == "higher-mae-low-mse"


def test_partial_validation_scope_is_not_exportable_or_selectable():
    partial_metrics = model.aggregate([
        {
            "status": "available",
            "delivery_days": 10,
            "expected_loss_mse": 0.10,
            "daily_expected_loss": [{"delivery_date": "2020-01-01", "expected_loss_mse": 0.10}],
        },
        {"status": "unavailable", "reason": "empty_eval", "fold": "2020-02-01__2020-03-01"},
    ])
    assert partial_metrics["status"] == "unavailable"
    assert partial_metrics["reason"] == "partial_validation_scope"

    summaries = [
        {
            "candidate_id": "partial-best",
            "model_family": "gam",
            "feature_group": "geometry",
            "exportable_stdlib": False,
            "status": "unavailable",
            "hyperparameters": {"logistic_C": 0.1, "gamma_alpha": 0.1},
            "rolling_metrics": partial_metrics,
        },
        {
            "candidate_id": "complete",
            "model_family": "gam",
            "feature_group": "geometry",
            "exportable_stdlib": True,
            "status": "available",
            "hyperparameters": {"logistic_C": 1.0, "gamma_alpha": 1.0},
            "rolling_metrics": {"status": "available", "expected_loss_mse": 0.30, "expected_loss_mse_se": 0.0},
        },
    ]

    selected = model.select_candidate(summaries, {"geometry": ["a"], "statistical": ["a", "b"], "joint": ["a", "b", "c"]})
    assert selected["selected_candidate_id"] == "complete"


def test_probability_calibration_solves_weighted_intercept_condition():
    labels = [1.0, 1.0, 0.0, 0.0]
    probs = [0.03, 0.55, 0.70, 0.97]
    weights = [3.0, 1.0, 1.0, 2.0]

    calibrator = model.calibrate_probability(labels, probs, weights)

    assert calibrator["status"] == "available"
    shifted = [inference.apply_probability_calibrator(prob, calibrator) for prob in probs]
    observed = sum(label * weight for label, weight in zip(labels, weights)) / sum(weights)
    calibrated_mean = sum(prob * weight for prob, weight in zip(shifted, weights)) / sum(weights)
    heuristic_delta = inference.logit(observed) - inference.logit(sum(prob * weight for prob, weight in zip(probs, weights)) / sum(weights))
    heuristic_mean = sum(inference.sigmoid(inference.logit(prob) + heuristic_delta) * weight for prob, weight in zip(probs, weights)) / sum(weights)

    assert calibrated_mean == pytest.approx(observed, abs=1e-12, rel=1e-12)
    assert abs(calibrator["first_order_residual"]) < 1e-12
    assert abs(heuristic_mean - observed) > 1e-3


def test_exported_gam_inference_identity_and_metadata(tmp_path):
    rows, gaps = model.enrich_rows(synthetic_rows(), model.DEFAULT_COMMON_FEATURE_GROUPS)
    assert not gaps
    protocol = small_protocol()
    rolling_dir = tmp_path / "rolling"
    run = model.train_run(rows, model.DEFAULT_COMMON_FEATURE_GROUPS, protocol, "2020-07-31", include_catboost=False, rolling_predictions_dir=rolling_dir)
    paths = model.write_outputs(tmp_path, run, model.DEFAULT_COMMON_FEATURE_GROUPS, protocol, {"status": "test"}, [write_csv(tmp_path, synthetic_rows(40))], [])
    artifact = load_artifact(paths["artifact"])
    selected = artifact["selected_feature_group"]
    prediction = predict_row(rows[-1], artifact)
    assert prediction["status"] == "available"
    assert prediction["model_hash"]
    assert prediction["training_cutoff_ms"]
    assert prediction["expected_loss_normalized"] == pytest.approx(
        prediction["probability_positive"] * prediction["conditional_positive_loss"], rel=1e-12, abs=1e-12
    )
    assert prediction["tail_probability"] == prediction["breach_probability"]
    assert prediction["tail_probability_status"] == "descriptive_only"
    assert artifact["models"][selected]["tail_summary"]["es95_all_normalized"] is not None
    assert artifact["models"][selected]["model_id"] == artifact["selection"]["selected_candidate_id"]
    for group, group_selection in artifact["group_selections"].items():
        assert artifact["models"][group]["model_id"] == group_selection["selected_candidate_id"]
    assert any(rolling_dir.glob("*.csv"))
    assert artifact["final_calibration_metrics"][selected]["expected_loss_mse"] is not None
    assert artifact["final_calibration_metrics"][selected]["expected_payout_btc_mse"] is not None


def test_final_refit_uses_one_se_selected_candidate_not_group_min_mse(monkeypatch):
    rows, gaps = model.enrich_rows(synthetic_rows(80), model.DEFAULT_COMMON_FEATURE_GROUPS)
    assert not gaps
    protocol = small_protocol(
        rolling_development_start="2020-04-01",
        rolling_development_end="2020-04-30",
        rolling_horizon_months=1,
        rolling_step_months=1,
        final_window_months=3,
        calibration_months=1,
        logistic_C=[0.1, 10.0],
    )
    calls = []

    def fake_train_candidate(_rows, spec, _groups, _protocol, rolling_predictions_dir=None):
        mse = 0.10 if spec.logistic_c == 10.0 else 0.105
        day_mse = [0.10, 0.10] if spec.logistic_c == 10.0 else [0.10, 0.11]
        return {
            "candidate_id": spec.candidate_id,
            "model_family": "gam",
            "feature_group": spec.group,
            "hyperparameters": {"logistic_C": spec.logistic_c, "gamma_alpha": spec.gamma_alpha},
            "status": "available",
            "exportable_stdlib": True,
            "rolling_metrics": {
                "status": "available",
                "expected_loss_mse": mse,
                "daily_expected_loss": [
                    {"delivery_date": "2020-04-10", "expected_loss_mse": day_mse[0], "row_count": 2},
                    {"delivery_date": "2020-04-20", "expected_loss_mse": day_mse[1], "row_count": 2},
                ],
                "seven_day_expected_loss_blocks": [
                    {"start_delivery_date": "2020-04-10", "end_delivery_date": "2020-04-16", "expected_loss_mse": day_mse[0]},
                    {"start_delivery_date": "2020-04-17", "end_delivery_date": "2020-04-23", "expected_loss_mse": day_mse[1]},
                ],
            },
            "fold_metrics": [],
            "rolling_prediction_path": None,
        }

    def fake_fit_gam(_fit, _cal, _names, spec, _protocol):
        calls.append(spec.candidate_id)
        return {
            "schema": "astra_joint_v11_gam@1.0.0",
            "status": "available",
            "model_id": spec.candidate_id,
            "model_family": "gam",
            "feature_group": spec.group,
            "feature_names": [],
            "required_feature_names": [],
            "preprocessor": {"trained_features": []},
            "classifier": {"kind": "constant", "constant_probability": 0.1},
            "positive_severity": {"kind": "constant", "constant_positive_loss": 0.1},
            "tail_head": {"status": "unavailable"},
            "calibrators": {},
            "eligibility": {"status": "available", "eligible": True, "qualified": True},
            "tail_summary": {},
        }

    monkeypatch.setattr(model, "train_candidate", fake_train_candidate)
    monkeypatch.setattr(model, "fit_gam", fake_fit_gam)

    run = model.train_run(rows, {"geometry": ["dte_hours"], "statistical": ["dte_hours"], "joint": ["dte_hours"]}, protocol, "2020-07-31", include_catboost=False)

    assert run["selection"]["best_by_mse_candidate_id"] == "gam__geometry__C10__A0.1"
    assert run["selection"]["selected_candidate_id"] == "gam__geometry__C0.1__A0.1"
    assert run["models"]["geometry"]["model_id"] == "gam__geometry__C0.1__A0.1"
    assert "gam__geometry__C10__A0.1" not in calls


def test_missing_patterns_are_reported_but_still_score_when_model_available(tmp_path):
    rows, _ = model.enrich_rows(synthetic_rows(), model.DEFAULT_COMMON_FEATURE_GROUPS)
    protocol = small_protocol()
    run = model.train_run(rows, model.DEFAULT_COMMON_FEATURE_GROUPS, protocol, "2020-07-31", include_catboost=False)
    paths = model.write_outputs(tmp_path, run, model.DEFAULT_COMMON_FEATURE_GROUPS, protocol, {"status": "test"}, [write_csv(tmp_path, synthetic_rows(40))], [])
    artifact = load_artifact(paths["artifact"])
    row = dict(rows[-1])
    selected = artifact["selected_feature_group"]
    missing_names = [
        item["name"]
        for item in artifact["models"][selected]["preprocessor"]["trained_features"]
        if item["name"] not in {"side_sign"}
    ][:2]
    assert missing_names
    for name in missing_names:
        row[name] = ""
    prediction = predict_row(row, artifact)
    assert prediction["status"] == "available"
    assert prediction["eligibility"]["qualified"] is False
    for name in missing_names:
        assert name in prediction["input_support"]["missing_features"]
    assert math.isfinite(prediction["expected_loss_normalized"])


def test_required_features_do_not_turn_optional_mechanism_gaps_into_unqualified_rows():
    names = [
        "dte_hours",
        "short_distance_fraction",
        "width_fraction",
        "side_sign",
        "ret_15",
        "ret_30",
        "vol_15",
        "vol_30",
        "net_flow_15",
        "net_flow_30",
        "ret_240",
        "vwap_deviation_15",
        "pressure_response_30",
    ]

    required = model.required_feature_names(names)

    assert set(required) == {
        "dte_hours",
        "short_distance_fraction",
        "width_fraction",
        "side_sign",
        "ret_15",
        "ret_30",
        "vol_15",
        "vol_30",
        "net_flow_15",
        "net_flow_30",
    }
    assert "ret_240" not in required
    assert "vwap_deviation_15" not in required


def test_missing_optional_catboost_feature_scores_but_stays_qualified():
    artifact = {
        "schema": "astra_joint_v11_model_artifact@1.0.0",
        "selected_feature_group": "unit",
        "models": {
            "unit": {
                "status": "available",
                "model_family": "catboost",
                "feature_group": "unit",
                "feature_names": ["ret_15", "side_sign", "vwap_deviation_15"],
                "required_feature_names": ["ret_15", "side_sign"],
                "model_id": "unit-catboost",
                "eligibility": {"status": "available", "eligible": True, "qualified": True},
                "tail_summary": {},
                "calibrators": {},
                "catboost_classifier": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "classifier",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [0.0]}],
                },
                "catboost_regressor": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "regressor",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [0.25]}],
                },
            }
        },
    }

    prediction = predict_row({"row_id": "M1", "side": "put_credit", "ret_15": 0.01, "side_sign": 1}, artifact)

    assert prediction["status"] == "available"
    assert prediction["eligibility"]["qualified"] is True
    assert "vwap_deviation_15" in prediction["input_support"]["missing_features"]
    assert prediction["expected_loss_normalized"] == pytest.approx(0.125)


def test_protection_breach_requires_explicit_leg_field():
    assert model.protection_breached({"loss_normalized": "1.5"}) is None
    assert model.protection_breached({"loss_normalized": "1.5", "protection_leg_breached": "false"}) is False
    assert model.protection_breached({"protection_breached": "true"}) is True


def test_missing_side_sign_fallback_matches_data_layer_convention():
    base = {
        "row_id": "x",
        "as_of_ms": ms(datetime(2020, 1, 1, tzinfo=timezone.utc)),
        "expiry_ms": ms(datetime(2020, 1, 1, 12, tzinfo=timezone.utc)),
        "delivery_date": "2020-01-01",
        "target_width": "2000",
        "actual_width": "2000",
        "entry_price": "50000",
        "loss_normalized": "0",
        "dte_hours": "12",
        "short_distance_fraction": "0.02",
        "width_fraction": "0.04",
    }
    rows, gaps = model.enrich_rows([{**base, "side": "put_credit"}, {**base, "row_id": "y", "side": "call_credit"}], model.DEFAULT_COMMON_FEATURE_GROUPS)

    assert not gaps
    assert rows[0]["side_sign"] == -1.0
    assert rows[1]["side_sign"] == 1.0


def test_time_split_purges_outcomes_not_completed_before_boundary():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime(2020, 2, 1, tzinfo=timezone.utc)
    rows = [
        {"_time_ms": ms(datetime(2020, 1, 15, tzinfo=timezone.utc)), "expiry_ms": ms(datetime(2020, 1, 16, tzinfo=timezone.utc)), "_delivery_date": "2020-01-16"},
        {"_time_ms": ms(datetime(2020, 1, 31, 12, tzinfo=timezone.utc)), "expiry_ms": ms(end), "_delivery_date": "2020-02-01"},
        {"_time_ms": ms(datetime(2020, 1, 31, 13, tzinfo=timezone.utc)), "expiry_ms": "", "_delivery_date": "2020-02-01"},
    ]

    kept, purge = model.between_with_purge(rows, start, end)

    assert kept == [rows[0]]
    assert purge["expiry_cross_rows"] == 1
    assert purge["missing_expiry_rows"] == 1


def test_delivery_date_overlap_is_removed_between_segments():
    fit = [{"_delivery_date": "2020-01-31"}, {"_delivery_date": "2020-01-30"}]
    cal = [{"_delivery_date": "2020-01-31"}, {"_delivery_date": "2020-02-01"}]

    filtered, meta = model.purge_overlapping_delivery_dates({"fit": fit, "calibration": cal})

    assert meta["overlapping_delivery_dates"] == ["2020-01-31"]
    assert filtered["fit"] == [fit[1]]
    assert filtered["calibration"] == [cal[1]]


def test_seven_day_blocks_are_calendar_blocks_with_date_equal_mean():
    blocks = model.seven_day_blocks([
        {"delivery_date": "2020-01-01", "expected_loss_mse": 1.0, "row_count": 100},
        {"delivery_date": "2020-01-03", "expected_loss_mse": 3.0, "row_count": 1},
        {"delivery_date": "2020-01-08", "expected_loss_mse": 8.0, "row_count": 4},
    ])

    assert blocks[0]["start_delivery_date"] == "2020-01-01"
    assert blocks[0]["end_delivery_date"] == "2020-01-07"
    assert blocks[0]["missing_day_count"] == 5
    assert blocks[0]["expected_loss_mse"] == pytest.approx(2.0)
    assert blocks[1]["start_delivery_date"] == "2020-01-08"
    assert blocks[1]["expected_loss_mse"] == pytest.approx(8.0)


def test_exported_preprocessor_has_stable_bspline_outputs():
    preprocessor = {
        "trained_features": [
            {
                "name": "x",
                "kind": "bspline",
                "median": 2.5,
                "mean": 0.0,
                "scale": 1.0,
                "degree": 2,
                "knots": [-2.5, -1.25, 0.0, 1.25, 2.5, 3.75, 5.0, 6.25, 7.5],
                "coefficients": [
                    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                ],
                "include_missing_indicator": True,
            }
        ]
    }
    vector, trace = transform_row({"x": 2.5}, preprocessor)
    assert vector == pytest.approx([0.0, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0], rel=1e-12, abs=1e-12)
    assert trace["features"][0]["missing"] is False


def test_portable_catboost_tree_predicts_without_catboost_dependency():
    artifact = {
        "schema": "astra_joint_v11_model_artifact@1.0.0",
        "selected_feature_group": "unit",
        "models": {
            "unit": {
                "status": "available",
                "model_family": "catboost",
                "feature_group": "unit",
                "feature_names": ["ret_15", "side_sign"],
                "required_feature_names": ["ret_15", "side_sign"],
                "model_id": "unit-catboost",
                "eligibility": {"status": "available", "eligible": True, "qualified": True},
                "tail_summary": {},
                "calibrators": {},
                "catboost_classifier": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "classifier",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [
                        {"splits": [{"feature_index": 0, "border": 0.2, "missing_goes_right": False}], "leaf_values": [-2.0, 2.0]}
                    ],
                },
                "catboost_regressor": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "regressor",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [0.5]}],
                },
                "catboost_tail": {
                    "status": "available",
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "tail",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [-1.0]}],
                },
            }
        },
    }

    prediction = predict_row({"row_id": "C1", "side": "call_credit", "ret_15": 0.4, "side_sign": -1}, artifact)

    assert prediction["status"] == "available"
    assert prediction["probability_positive"] == pytest.approx(1.0 / (1.0 + math.exp(-2.0)))
    assert prediction["conditional_positive_loss"] == pytest.approx(0.5)
    assert prediction["expected_loss_normalized"] == pytest.approx(prediction["probability_positive"] * 0.5)
    assert prediction["breach_probability"] == pytest.approx(1.0 / (1.0 + math.exp(1.0)))


def test_publication_policy_hides_research_only_tail_probability_without_changing_main_prediction():
    base_artifact = {
        "schema": "astra_joint_v11_model_artifact@1.0.0",
        "selected_feature_group": "unit",
        "models": {
            "unit": {
                "status": "available",
                "model_family": "catboost",
                "feature_group": "unit",
                "feature_names": ["ret_15", "side_sign"],
                "required_feature_names": ["ret_15", "side_sign"],
                "model_id": "unit-catboost",
                "eligibility": {"status": "available", "eligible": True, "qualified": True},
                "tail_summary": {},
                "calibrators": {},
                "catboost_classifier": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "classifier",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [0.25]}],
                },
                "catboost_regressor": {
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "regressor",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [0.4]}],
                },
                "catboost_tail": {
                    "status": "available",
                    "format": "catboost_oblivious_trees_json@1.0.0",
                    "role": "tail",
                    "scale": 1.0,
                    "bias": 0.0,
                    "oblivious_trees": [{"splits": [], "leaf_values": [1.5]}],
                },
            }
        },
    }
    policy_artifact = {
        **base_artifact,
        "publication_policy": {
            "tail_probability": "research_only",
            "tail_probability_reason": "tail head failed nested-probability audit",
            "tail_probability_evidence_hash": "unit-test",
        },
    }

    row = {"row_id": "P1", "side": "put_credit", "ret_15": 0.1, "side_sign": -1, "actual_width": "2000", "entry_price": "50000"}
    normal = predict_row(row, base_artifact)
    hidden = predict_row(row, policy_artifact)

    assert normal["tail_probability"] is not None
    assert hidden["tail_probability"] is None
    assert hidden["breach_probability"] is None
    assert hidden["raw_tail_probability"] is None
    assert hidden["tail_probability_status"] == "research_only"
    assert hidden["probability_positive"] == pytest.approx(normal["probability_positive"])
    assert hidden["conditional_positive_loss"] == pytest.approx(normal["conditional_positive_loss"])
    assert hidden["expected_loss_normalized"] == pytest.approx(normal["expected_loss_normalized"])
    assert hidden["expected_payout_btc"] == pytest.approx(normal["expected_payout_btc"])
    trace = hidden["preprocess_trace"]["research_only_tail_probability"]
    assert trace["tail_probability"] == pytest.approx(normal["tail_probability"])
    assert trace["raw_tail_probability"] == pytest.approx(normal["raw_tail_probability"])
    assert trace["evidence_hash"] == "unit-test"


def test_portable_catboost_uses_float32_split_semantics_and_missing_branch():
    tree = {
        "format": "catboost_oblivious_trees_json@1.0.0",
        "role": "classifier",
        "scale": 1.0,
        "bias": 0.0,
        "oblivious_trees": [
            {"splits": [{"feature_index": 0, "border": 0.1, "missing_goes_right": False}], "leaf_values": [-1.0, 1.0]},
        ],
    }
    raw_at_border, trace_at_border = inference._predict_catboost_raw(tree, [math.nextafter(0.1, math.inf)])
    raw_above, trace_above = inference._predict_catboost_raw(tree, [inference.float32(0.1) + 1e-6])
    raw_missing_left, trace_missing_left = inference._predict_catboost_raw(tree, [None])
    tree["oblivious_trees"][0]["splits"][0]["missing_goes_right"] = True
    raw_missing_right, trace_missing_right = inference._predict_catboost_raw(tree, [None])

    assert raw_at_border == pytest.approx(-1.0)
    assert trace_at_border["leaf_indexes"] == [0]
    assert raw_above == pytest.approx(1.0)
    assert trace_above["leaf_indexes"] == [1]
    assert raw_missing_left == pytest.approx(-1.0)
    assert trace_missing_left["leaf_indexes"] == [0]
    assert raw_missing_right == pytest.approx(1.0)
    assert trace_missing_right["leaf_indexes"] == [1]


def test_real_catboost_export_matches_native_when_available():
    np = pytest.importorskip("numpy")
    pytest.importorskip("catboost")
    groups = {"geometry": ["ret_15", "side_sign"], "statistical": ["ret_15", "side_sign"], "joint": ["ret_15", "side_sign"]}
    raw_rows = synthetic_rows(80)
    rows, gaps = model.enrich_rows(raw_rows, groups)
    assert not gaps
    protocol = small_protocol(min_tail_delivery_days_for_precise_probability=1, catboost_max_iterations=20)
    fitted = model.fit_catboost(rows[:120], rows[120:150], groups["geometry"], model.CandidateSpec("catboost", "geometry", depth=3), protocol)
    assert fitted["status"] == "available"
    artifact = {
        "schema": "astra_joint_v11_model_artifact@1.0.0",
        "selected_feature_group": "geometry",
        "models": {"geometry": model.strip_runtime(fitted)},
    }
    test_rows = rows[150:155]
    X = np.asarray(model.catboost_matrix(test_rows, groups["geometry"]), dtype=float)
    native_probability = fitted["_runtime_classifier"].predict_proba(X)[:, 1]
    native_loss = fitted["_runtime_regressor"].predict(X)
    for index, row in enumerate(test_rows):
        exported = predict_row(row, artifact)
        assert exported["raw_probability_positive"] == pytest.approx(float(native_probability[index]), abs=1e-8, rel=1e-8)
        assert exported["raw_conditional_positive_loss"] == pytest.approx(float(native_loss[index]), abs=1e-8, rel=1e-8)
        assert exported["expected_loss_normalized"] == pytest.approx(
            exported["probability_positive"] * exported["conditional_positive_loss"], abs=1e-12, rel=1e-12
        )


def test_catboost_candidate_path_is_available_when_package_exists():
    pytest.importorskip("catboost")
    groups = {"geometry": ["ret_15", "side_sign"], "statistical": ["ret_15", "side_sign"], "joint": ["ret_15", "side_sign"]}
    rows, _ = model.enrich_rows(synthetic_rows(180), groups)
    protocol = small_protocol(
        rolling_development_start="2020-04-01",
        rolling_development_end="2020-04-30",
        rolling_horizon_months=1,
        rolling_step_months=1,
        final_window_months=3,
        calibration_months=1,
        catboost_max_iterations=10,
    )
    summary = model.train_candidate(rows, model.CandidateSpec("catboost", "geometry", depth=3), groups, protocol)
    assert summary["model_family"] == "catboost"
    assert summary["status"] == "available"
    assert summary["exportable_stdlib"] is True
