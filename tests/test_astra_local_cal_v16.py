from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import astra_local_cal_v16 as local


def utc_ms(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def row(day, side="put", state="RANGE", loss=0.1, mu=0.08, short=100.0, long=95.0):
    entry = datetime(2022, 1, 1, 8, tzinfo=timezone.utc) + timedelta(days=day)
    expiry = entry + timedelta(hours=12)
    return {
        "row_id": f"{side}-{state}-{day}",
        "event_family": "test",
        "observation_kind": "clock_30m",
        "as_of_ms": utc_ms(entry),
        "entry_ms": utc_ms(entry),
        "expiry_ms": utc_ms(expiry),
        "delivery_date": expiry.date().isoformat(),
        "side": side,
        "actual_width": abs(short - long),
        "short_strike": short,
        "long_strike": long,
        "entry_price": 100.0,
        "dte_hours": 12.0,
        "loss_normalized": loss,
        "mu_stat": mu,
        "mu_geom": mu,
        "regime": state,
        "rv4_annualized": 0.8,
    }


def synthetic_base():
    rows = []
    for d in range(65):
        rows.append(row(d, "put", "RANGE", loss=0.12, mu=0.08))
        rows.append(row(d, "call", "RANGE", loss=0.04, mu=0.05, short=100.0, long=105.0))
    for d in range(90, 116):
        rows.append(row(d, "put", "RANGE", loss=0.13, mu=0.08))
        rows.append(row(d, "call", "RANGE", loss=0.03, mu=0.05, short=100.0, long=105.0))
    for d in range(181, 190):
        rows.append(row(d, "put", "RANGE", loss=0.11, mu=0.08))
        rows.append(row(d, "call", "UP", loss=0.03, mu=0.05, short=100.0, long=105.0))
    return pd.DataFrame(rows)


def test_h2_label_changes_do_not_change_q1_calibration_or_q2_permission():
    frame, _ = local.prepare_utc08(synthetic_base())
    params = local.fit_q1_parameters(frame)
    permissions = local.select_permissions(frame, params)
    changed = frame.copy()
    changed.loc[changed.split.eq("H2"), "loss_normalized"] = 9.0
    params_changed = local.fit_q1_parameters(changed)
    permissions_changed = local.select_permissions(changed, params_changed)

    pd.testing.assert_frame_equal(
        params.sort_index(axis=1).reset_index(drop=True),
        params_changed.sort_index(axis=1).reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        permissions[["year", "side", "state", "allowed", "q2_residual_after_global"]].reset_index(drop=True),
        permissions_changed[["year", "side", "state", "allowed", "q2_residual_after_global"]].reset_index(drop=True),
    )


def test_sparse_state_falls_back_to_global_prediction():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=0.1, mu=0.2)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": False,
                "b_global": 0.03,
                "local_adjustment": 0.5,
            }
        ]
    )
    got = local.attach_predictions(frame, params, pd.DataFrame(columns=["year", "side", "state", "allowed"]))

    assert got.loc[0, "mu_global"] == pytest.approx(0.23)
    assert got.loc[0, "mu_local_all_supported"] == pytest.approx(got.loc[0, "mu_global"])
    assert not bool(got.loc[0, "local_all_supported_cell"])


def test_mixed_and_unknown_use_same_side_year_global_intercept():
    frame, _ = local.prepare_utc08(
        pd.DataFrame(
            [
                row(181, "put", "MIXED", loss=0.1, mu=0.2),
                row(182, "put", "UNKNOWN", loss=0.1, mu=0.2),
            ]
        )
    )
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": True,
                "b_global": 0.03,
                "local_adjustment": 0.5,
            }
        ]
    )
    got = local.attach_predictions(frame, params, pd.DataFrame(columns=["year", "side", "state", "allowed"]))

    assert got["mu_global"].tolist() == pytest.approx([0.23, 0.23])
    assert got["mu_local_all_supported"].tolist() == pytest.approx([0.23, 0.23])
    assert not got["local_all_supported_cell"].any()


def test_missing_base_prediction_remains_unknown_not_zero():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=0.1, mu=np.nan)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": True,
                "b_global": -0.2,
                "local_adjustment": 0.1,
            }
        ]
    )
    got = local.attach_predictions(frame, params, pd.DataFrame([{"year": 2022, "side": "put", "state": "RANGE", "allowed": True}]))

    assert np.isnan(got.loc[0, "mu_global"])
    assert np.isnan(got.loc[0, "mu_local_all_supported"])
    assert np.isnan(got.loc[0, "mu_local_embed"])


def test_global_min60_shortfall_blocks_local_all_even_when_state_supported():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=0.1, mu=0.2)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": False,
                "q1_state_supported": True,
                "b_global": 0.03,
                "local_adjustment": -0.1,
            }
        ]
    )
    got = local.attach_predictions(frame, params, pd.DataFrame(columns=["year", "side", "state", "allowed"]))

    assert got.loc[0, "mu_global"] == pytest.approx(0.23)
    assert got.loc[0, "mu_local_all_supported"] == pytest.approx(0.23)
    assert not bool(got.loc[0, "local_all_supported_cell"])


def test_predictions_are_lower_bounded_but_loss_tail_is_not_clipped():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=1.5, mu=-1.0, short=100.0, long=50.0)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": True,
                "b_global": -0.2,
                "local_adjustment": 0.0,
            }
        ]
    )
    permissions = pd.DataFrame([{"year": 2022, "side": "put", "state": "RANGE", "allowed": True}])
    ledger, summaries, _ = local.evaluate_h2(frame, params, permissions)
    original = next(x for x in summaries if x.get("policy") == "ORIGINAL" and x.get("scenario") == "IV80" and x.get("side") == "put")

    assert ledger["GLOBAL_CAL_prediction"].min() == pytest.approx(0.0)
    assert ledger["loss_normalized"].max() == pytest.approx(1.5)
    assert original["max_net_loss"] > 1.0


def test_local_only_non_allowed_rows_are_cash_zero():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=1.5, mu=0.0, short=100.0, long=50.0)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": True,
                "b_global": 0.0,
                "local_adjustment": 0.0,
            }
        ]
    )
    permissions = pd.DataFrame([{"year": 2022, "side": "put", "state": "RANGE", "allowed": False}])
    ledger, _summaries, _ = local.evaluate_h2(frame, params, permissions)

    assert ledger.loc[0, "LOCAL_ONLY_prediction"] == pytest.approx(ledger.loc[0, "GLOBAL_CAL_prediction"])
    assert not bool(ledger.loc[0, "LOCAL_ONLY_action"])
    assert ledger.loc[0, "LOCAL_ONLY_pnl"] == pytest.approx(0.0)


def test_local_only_uses_real_prediction_mse_without_fake_cash_prediction():
    frame, _ = local.prepare_utc08(pd.DataFrame([row(181, "put", "RANGE", loss=0.25, mu=0.2)]))
    params = pd.DataFrame(
        [
            {
                "year": 2022,
                "side": "put",
                "state": "RANGE",
                "eligible_cell": True,
                "q1_global_supported": True,
                "q1_state_supported": True,
                "b_global": 0.03,
                "local_adjustment": -0.1,
            }
        ]
    )
    permissions = pd.DataFrame([{"year": 2022, "side": "put", "state": "RANGE", "allowed": False}])
    ledger, summaries, _ = local.evaluate_h2(frame, params, permissions)
    global_metric = next(x for x in summaries if x.get("policy") == "GLOBAL_CAL" and x.get("scenario") == "IV60")
    local_only_metric = next(x for x in summaries if x.get("policy") == "LOCAL_ONLY" and x.get("scenario") == "IV60")

    assert ledger.loc[0, "LOCAL_ONLY_prediction"] == pytest.approx(ledger.loc[0, "GLOBAL_CAL_prediction"])
    assert local_only_metric["mse_loss_prediction"] == pytest.approx(global_metric["mse_loss_prediction"])
    assert local_only_metric["known_net_contribution_per_all_original_weight"] == pytest.approx(0.0)
    assert local_only_metric["action_weight_fraction"] == pytest.approx(0.0)


def test_unsealed_protocol_does_not_start_run(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    (research / "L02_amendment.json").write_text(json.dumps({"schema": "astra_underwriting_v16_L02_amendment@1.0.0"}), encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        local.run(tmp_path, research, "local_01")
    assert not (research / "local_01").exists()


def test_tiny_end_to_end_run_writes_metadata_columns_and_both_sides(tmp_path):
    research = tmp_path / "research"
    run_01 = research / "run_01"
    run_01.mkdir(parents=True)
    synthetic_base().to_csv(run_01 / "base_rows.csv", index=False)
    amendment = {"schema": "astra_underwriting_v16_L02_amendment@1.0.0"}
    (research / "L02_amendment.json").write_text(json.dumps(amendment), encoding="utf-8")
    seal = {
        "amendment_sha256": local.core.digest(research / "L02_amendment.json"),
        "local_calibration_results_computed_before_seal": False,
    }
    (research / "L02_seal.json").write_text(json.dumps(seal), encoding="utf-8")

    receipt = local.run(tmp_path, research, "local_01")
    out = research / "local_01"
    ledger = pd.read_csv(out / "h2_policy_ledger.csv")
    params = json.loads((out / "calibration_params.json").read_text("utf-8"))
    support = json.loads((out / "permission_support.json").read_text("utf-8"))

    assert receipt["schema"] == local.SEAL_SCHEMA
    assert receipt["new_training_jobs"] == 0
    assert receipt["base_model_retraining_jobs"] == 0
    assert receipt["residual_calibration_side_years"] == 8
    assert set(ledger["side"]) == {"put", "call"}
    assert set(ledger["scenario"]) == set(local.core.SCENARIOS)
    assert {"GLOBAL_CAL_pnl", "LOCAL_EMBED_pnl", "LOCAL_ONLY_action"}.issubset(ledger.columns)
    assert len(params) == 24
    assert len(support) == 24
    reproduce = (out / "reproduce_command.txt").read_text("utf-8")
    assert '"' in reproduce
    assert "--run-name local_01" in reproduce
    assert "fresh --run-name" in reproduce
