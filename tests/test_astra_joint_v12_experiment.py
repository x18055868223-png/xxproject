from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_v12_experiment as experiment
from astra_joint_v12_inference import predict_nested_tail, predict_pair_delta


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _empty_pair_model(delta: float) -> dict[str, object]:
    return {
        "kind": "pair_ridge_delta",
        "candidate_id": "PAIR_RIDGE10",
        "preprocessor": {"features": []},
        "linear_model": {"intercept": delta, "coefficients": []},
        "calibrator": {"status": "available", "residual_intercept": 0.0},
    }


def _empty_tail_model() -> dict[str, object]:
    return {
        "status": "available",
        "kind": "nested_conditional_tail_logistic",
        "candidate_id": "NESTED_LOGIT_C01",
        "preprocessor": {"features": []},
        "conditional_tail_model": {"intercept": 0.0, "coefficients": []},
        "calibrator": {"status": "unavailable"},
    }


def _protocol() -> dict[str, object]:
    return {
        "schema": "unit",
        "scope": "unit",
        "production": {"publish_authorized": False},
        "final_fit_cutoff": "2026-08-31",
        "candidates": {
            "S50": {"alpha": 0.5},
            "PAIR_RIDGE10": {"alpha": 10.0},
            "NESTED_LOGIT_C01": {"C": 0.1, "min_tail_days_fit": 1, "min_tail_days_calibration": 1},
        },
        "folds": {"lookback_months": 24, "fit_months": 21, "calibration_months": 3, "years": [2022, 2023]},
        "stop_conditions": ["unit"],
        "evaluation": {"bootstrap_seed": 20260921},
    }


def test_nested_parent_probability_keeps_zero_and_one_and_rejects_bad_values():
    model = _empty_tail_model()

    zero = predict_nested_tail({"row_id": "z", "parent_probability_positive": 0.0}, model)
    one = predict_nested_tail({"row_id": "o", "parent_probability_positive": 1.0}, model)
    fallback_zero = predict_nested_tail({"row_id": "fz", "parent_probability_positive": 0.0, "statistical_probability_positive": 0.7}, model)
    missing = predict_nested_tail({"row_id": "m"}, model)
    high = predict_nested_tail({"row_id": "h", "parent_probability_positive": 1.01}, model)
    low = predict_nested_tail({"row_id": "l", "parent_probability_positive": -0.01}, model)

    assert zero["status"] == "available"
    assert zero["parent_probability_positive"] == 0.0
    assert zero["tail_probability"] == 0.0
    assert one["parent_probability_positive"] == 1.0
    assert one["tail_probability"] == pytest.approx(0.5)
    assert fallback_zero["tail_probability"] == 0.0
    assert missing["status"] == "unavailable"
    assert high["reason"] == "parent_probability_positive_out_of_range"
    assert low["reason"] == "parent_probability_positive_out_of_range"


def test_vectorized_nested_parent_probability_matches_stdlib_zero_one_and_failures():
    model = _empty_tail_model()
    frame = pd.DataFrame(
        [
            {
                "row_id": "zero",
                "observation_id": "oz",
                "delivery_date": "2022-01-01",
                "as_of_ms": 1,
                "expiry_ms": 2,
                "side": "put_credit",
                "actual_loss_normalized": 0.0,
                "protection_breached_bool": False,
                "statistical_probability_positive": 0.0,
            },
            {
                "row_id": "one",
                "observation_id": "oo",
                "delivery_date": "2022-01-02",
                "as_of_ms": 3,
                "expiry_ms": 4,
                "side": "call_credit",
                "actual_loss_normalized": 1.2,
                "protection_breached_bool": True,
                "statistical_probability_positive": 1.0,
            },
        ]
    )

    vectorized = experiment.nested_tail_predictions(frame, model).set_index("row_id")
    for _, row in frame.iterrows():
        stdlib = predict_nested_tail({**row.to_dict(), "parent_probability_positive": row["statistical_probability_positive"]}, model)
        assert vectorized.loc[row["row_id"], "parent_probability_positive"] == pytest.approx(stdlib["parent_probability_positive"])
        assert vectorized.loc[row["row_id"], "tail_probability"] == pytest.approx(stdlib["tail_probability"])
    assert vectorized.loc["zero", "tail_probability"] == 0.0
    assert vectorized.loc["one", "tail_probability"] == pytest.approx(0.5)

    with pytest.raises(ValueError, match="missing_parent_probability_positive"):
        experiment.nested_tail_predictions(frame.assign(statistical_probability_positive=[0.0, None]), model)
    with pytest.raises(ValueError, match="parent_probability_positive_out_of_range"):
        experiment.nested_tail_predictions(frame.assign(statistical_probability_positive=[0.0, 1.01]), model)


def test_pair_delta_is_put_minus_call_and_not_single_side_expected_loss():
    put_better = predict_pair_delta({"observation_id": "p", "baseline_pair_center_loss": 0.4}, _empty_pair_model(-0.2))
    call_better = predict_pair_delta({"observation_id": "c", "baseline_pair_center_loss": 0.4}, _empty_pair_model(0.2))

    assert put_better["selected_side"] == "put_credit"
    assert call_better["selected_side"] == "call_credit"
    assert "put_expected_loss_normalized" not in put_better
    assert "call_expected_loss_normalized" not in put_better

    frame = pd.DataFrame(
        {
            "delivery_date": ["2022-01-01", "2022-01-02"],
            "actual_put": [0.0, 1.0],
            "actual_call": [1.0, 0.0],
        }
    )
    metrics = experiment.side_metrics_delta(frame, [-0.5, 0.5])
    assert metrics["selected_actual_loss"] == pytest.approx(0.0)
    assert metrics["pair_delta_mse"] == pytest.approx(0.25)


def test_equal_mix_random_single_side_es_differs_from_simultaneous_average_and_fixed_mse_is_null():
    frame = pd.DataFrame(
        {
            "delivery_date": ["2022-01-01", "2022-01-01"],
            "actual_put": [0.0, 0.0],
            "actual_call": [1.0, 1.0],
        }
    )

    mix = experiment.equal_side_mix_metrics(frame)
    fixed_put = experiment.fixed_direction_metrics(frame, "put_credit")
    fixed_call = experiment.fixed_direction_metrics(frame, "call_credit")

    assert mix["selected_actual_loss"] == pytest.approx(0.5)
    assert mix["selected_es95"] == pytest.approx(1.0)
    assert mix["simultaneous_averaged_two_side_portfolio"]["selected_actual_loss"] == pytest.approx(0.5)
    assert mix["simultaneous_averaged_two_side_portfolio"]["selected_es95"] == pytest.approx(0.5)
    assert fixed_put["pair_delta_mse"] is None
    assert fixed_put["paired_row_mse"] is None
    assert fixed_call["pair_delta_mse"] is None
    assert fixed_call["paired_row_mse"] is None
    assert fixed_put["control_kind"] == "fixed_direction_not_prediction_model"
    assert fixed_put["model_error_reason"]


def test_fit_exports_match_stdlib_inference_for_pair_and_tail():
    pair_frame = pd.DataFrame(
        {
            "delivery_date": [f"2022-01-{day:02d}" for day in range(1, 13)],
            "put_short_distance_fraction": [0.01 * day for day in range(1, 13)],
            "call_short_distance_fraction": [0.02] * 12,
            "width_fraction": [0.04] * 12,
            "dte_hours": [12.0] * 12,
            "ret_30": [(-1) ** day * 0.01 for day in range(1, 13)],
            "ret_240": [0.002 * day for day in range(1, 13)],
            "ret_1440": [0.0] * 12,
            "vol_30": [0.01] * 12,
            "vol_240": [0.02] * 12,
            "net_flow_30": [0.1 * day for day in range(1, 13)],
            "net_flow_240": [0.2 * day for day in range(1, 13)],
            "actual_delta": [(-1) ** day * 0.2 for day in range(1, 13)],
        }
    )
    pair_model = experiment.fit_pair_ridge(pair_frame.iloc[:10], pair_frame.iloc[10:], _protocol())
    row = pair_frame.iloc[0].to_dict()
    native = experiment.predict_linear_frame(pd.DataFrame([row]), pair_model["preprocessor"], pair_model["linear_model"]["intercept"], pair_model["linear_model"]["coefficients"])[0]
    native += pair_model["calibrator"]["residual_intercept"]
    exported = predict_pair_delta(row, pair_model)
    assert exported["predicted_put_minus_call_loss"] == pytest.approx(native, abs=1e-10)

    tail_rows = []
    for day in range(1, 25):
        positive = day % 2 == 0
        tail_rows.append(
            {
                "row_id": f"tail-{day}",
                "observation_id": f"tail-obs-{day}",
                "as_of_ms": day,
                "expiry_ms": day + 1,
                "side": "put_credit" if day % 2 else "call_credit",
                "delivery_date": f"2022-02-{(day - 1) % 20 + 1:02d}",
                "actual_loss_normalized": 0.3 if positive else 0.0,
                "protection_breached_bool": positive and day % 4 == 0,
                "dte_hours": 12.0,
                "short_distance_fraction": 0.01 * day,
                "width_fraction": 0.04,
                "side_sign": -1.0 if day % 2 else 1.0,
                "ret_30": 0.001 * day,
                "ret_240": 0.002 * day,
                "vol_30": 0.01,
                "vol_240": 0.02,
                "net_flow_30": 0.1 * day,
                "net_flow_240": 0.2 * day,
                "statistical_probability_positive": 0.3,
            }
        )
    tail_frame = pd.DataFrame(tail_rows)
    tail_model = experiment.fit_nested_tail(tail_frame.iloc[:18], tail_frame.iloc[18:], _protocol())
    tail_row = tail_frame.iloc[1].to_dict()
    vectorized = experiment.nested_tail_predictions(pd.DataFrame([tail_row]), tail_model).iloc[0]
    stdlib = predict_nested_tail({**tail_row, "parent_probability_positive": tail_row["statistical_probability_positive"]}, tail_model)
    assert stdlib["tail_probability"] == pytest.approx(vectorized["tail_probability"], abs=1e-10)
    assert stdlib["order_violation"] is False


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _build_synthetic_research(root: Path) -> None:
    model_rows_by_year: dict[int, list[dict[str, object]]] = {year: [] for year in range(2020, 2027)}
    prediction_rows = {name: [] for name in experiment.V11_CONTROL_FILES}
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for offset in range((datetime(2026, 9, 1, tzinfo=timezone.utc) - start).days):
        as_of = start + timedelta(days=offset)
        expiry = as_of + timedelta(hours=12)
        delivery = expiry.strftime("%Y-%m-%d")
        year = int(delivery[:4])
        observation_id = f"obs-{as_of:%Y%m%d}"
        for side_name, side_sign in (("put", -1.0), ("call", 1.0)):
            other = "call" if side_name == "put" else "put"
            positive = (offset + (0 if side_name == "put" else 2)) % 5 == 0
            tail = positive and (offset % 11 == 0 or offset % 13 == 0)
            loss = 1.2 if tail else (0.25 if positive else 0.0)
            row_id = f"{as_of:%Y%m%d}-{side_name}"
            base = {
                "row_id": row_id,
                "observation_id": observation_id,
                "as_of_ms": _ms(as_of),
                "entry_ms": _ms(as_of),
                "expiry_ms": _ms(expiry),
                "delivery_date": delivery,
                "side": side_name,
                "target_width": 2000.0,
                "actual_width": 2000.0,
                "entry_price": 50000.0,
                "loss_normalized": loss,
                "payout_btc": loss * 2000.0 / 50000.0,
                "protection_leg_breached": tail,
                "protection_breached": tail,
                "short_leg_breached": positive,
                "dte_hours": 12.0,
                "short_distance_fraction": 0.015 + (offset % 17) * 0.0005 + (0.001 if other == "put" else 0.0),
                "width_fraction": 0.04,
                "side_sign": side_sign,
                "ret_30": math.sin(offset / 9) * 0.01,
                "ret_240": math.sin(offset / 11) * 0.02,
                "ret_1440": math.cos(offset / 13) * 0.03,
                "vol_30": 0.01 + (offset % 5) * 0.001,
                "vol_240": 0.02 + (offset % 7) * 0.001,
                "net_flow_30": side_sign * math.sin(offset / 7),
                "net_flow_240": side_sign * math.cos(offset / 8),
            }
            if year in model_rows_by_year:
                model_rows_by_year[year].append(base)
            if 2022 <= year <= 2025:
                fold = f"{year}-01-01__{year + 1}-01-01"
                for key, filename in experiment.V11_CONTROL_FILES.items():
                    expected = 0.12 + (0.03 if positive else 0.0)
                    if key == "statistical":
                        expected = 0.10 + (0.02 if positive else 0.0)
                    if key == "joint":
                        expected = 0.11 + (0.025 if positive else 0.0)
                    probability = 0.20 + (0.20 if positive else 0.0)
                    prediction_rows[key].append(
                        {
                            "candidate_id": filename.removesuffix(".csv"),
                            "fold": fold,
                            "feature_group": key,
                            "model_family": "unit",
                            "model_id": filename.removesuffix(".csv"),
                            "row_id": row_id,
                            "observation_id": observation_id,
                            "as_of_ms": _ms(as_of),
                            "entry_ms": _ms(as_of),
                            "expiry_ms": _ms(expiry),
                            "delivery_date": delivery,
                            "side": f"{side_name}_credit",
                            "target_width": 2000.0,
                            "actual_width": 2000.0,
                            "expected_loss_normalized": expected,
                            "probability_positive": probability,
                            "conditional_positive_loss": expected / probability,
                            "breach_probability": min(probability, 0.05 + (0.10 if tail else 0.0)),
                            "tail_probability_status": "descriptive_only",
                            "actual_loss_normalized": loss,
                            "actual_payout_btc": loss * 2000.0 / 50000.0,
                            "protection_breached": tail,
                        }
                    )
    for year, rows in model_rows_by_year.items():
        _write_csv(root / "step30" / "model_input" / f"model_rows-{year}.csv", rows)
    pred_dir = root / "step30" / "models_annual_calibrated_20260921" / "rolling_candidate_predictions"
    for key, filename in experiment.V11_CONTROL_FILES.items():
        _write_csv(pred_dir / filename, prediction_rows[key])


def test_run_experiment_synthetic_outputs_are_research_only(tmp_path):
    research = tmp_path / "research"
    _build_synthetic_research(research)
    protocol_path = tmp_path / "protocol_v12.json"
    protocol_path.write_text(json.dumps(_protocol(), ensure_ascii=False), encoding="utf-8")
    seal_path = tmp_path / "protocol_seal.json"
    protocol_hash = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps({"schema": "unit", "protocol_sha256": protocol_hash}), encoding="utf-8")

    out = tmp_path / "experiment"
    summary = experiment.run_experiment(research, out, protocol_path, seal_path, bootstrap_repetitions=11)

    assert summary["qualification"] == "research_only_no_replacement"
    assert summary["final_model_status"]["S50"] == "available_no_refit"
    assert Path(summary["output_paths"]["row_predictions_csv"]).exists()
    artifact = json.loads(Path(summary["output_paths"]["model_artifact_json"]).read_text(encoding="utf-8"))
    assert artifact["models"]["NESTED_LOGIT_C01"]["tail_probability_status"] == "research_only"
    assert artifact["models"]["PAIR_RIDGE10"]["kind"] == "pair_ridge_delta"
    pair_rows = list(csv.DictReader(Path(summary["output_paths"]["pair_predictions_csv"]).open("r", encoding="utf-8")))
    assert pair_rows
    assert "put_expected_loss_normalized" not in pair_rows[0]
