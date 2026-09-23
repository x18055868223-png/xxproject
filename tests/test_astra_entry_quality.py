from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_entry_quality as eq


def _protocol(min_days: int = 20) -> dict[str, object]:
    return {
        "identity": {"years": [2022], "fit_source_years": [2020, 2021, 2022]},
        "opportunity": {
            "primary_coverage": 0.5,
            "curve_coverages": [0.5],
            "dte_edges": [8.0, 16.0, 24.0],
            "distance_edges": [0.0025, 0.005, 0.01, 0.02],
            "risk_display_edges": [0.0, 0.05, 0.1, 0.2, 0.4, 1.0],
            "minimum_stratum_days": min_days,
        },
        "uncertainty": {"replicates": 5, "seed": 7, "calendar_block_days": 7},
    }


def _frame(days: int = 21) -> pd.DataFrame:
    rows = []
    for day in range(days):
        rows.append(
            {
                "row_id": f"r{day}",
                "delivery_date": f"2022-01-{day + 1:02d}",
                "side": "put",
                "actual_width": 2000.0,
                "dte_hours": 12.0,
                "short_distance_fraction": 0.006,
                "vol_240": 0.02,
                "actual_loss_normalized": 0.0,
                "protection_breached_bool": False,
                "geometry_expected_loss_normalized": 0.2,
                "statistical_expected_loss_normalized": 0.2,
                "joint_expected_loss_normalized": 0.2,
            }
        )
    frame = pd.DataFrame(rows)
    frame["row_weight"] = eq.side_day_weights(frame)
    frame["stratum_key"] = "s"
    return frame


def test_weighted_fractional_ties_share_same_fraction_without_label_breaking():
    frame = _frame(21)
    frame = frame.iloc[:3].copy()
    frame["delivery_date"] = ["2022-01-01", "2022-01-02", "2022-01-03"]
    frame["row_weight"] = [0.4, 0.4, 0.2]
    frame["score"] = [0.1, 0.1, 0.9]
    frame["actual_loss_normalized"] = [9.0, 0.0, 0.0]

    membership, meta = eq.allocate_fractional_coverage(frame, "score", 0.5, min_stratum_days=1)

    assert membership.iloc[0] == pytest.approx(0.625)
    assert membership.iloc[1] == pytest.approx(0.625)
    assert membership.iloc[2] == pytest.approx(0.0)
    assert meta[0]["partial_tie_groups"] == 1
    assert float((membership * frame["row_weight"]).sum()) == pytest.approx(0.5)


def test_sparse_stratum_uses_uniform_fractional_coverage_for_all_methods():
    frame = _frame(3)
    frame["score"] = [0.0, 0.5, 1.0]
    membership, meta = eq.allocate_fractional_coverage(frame, "score", 0.4, min_stratum_days=20)

    assert membership.tolist() == pytest.approx([0.4, 0.4, 0.4])
    assert meta[0]["sparse_uniform"] is True


def test_selected_deferred_weight_and_loss_conservation():
    frame = _frame(2)
    frame["actual_loss_normalized"] = [0.0, 2.0]
    frame["row_weight"] = [0.5, 0.5]
    membership = pd.Series([1.0, 0.0], index=frame.index)

    metrics = eq.membership_metrics(frame, membership)

    assert metrics["coverage"] == pytest.approx(0.5)
    assert metrics["conditional_mean_loss_selected"] == pytest.approx(0.0)
    assert metrics["conditional_mean_loss_deferred"] == pytest.approx(2.0)
    assert metrics["weight_conservation_residual"] == pytest.approx(0.0)
    assert metrics["loss_conservation_residual"] == pytest.approx(0.0)
    assert metrics["deferred_zero_payout_fraction_of_deferred"] == pytest.approx(0.0)


def test_side_day_weights_make_each_side_delivery_day_equal():
    frame = pd.DataFrame(
        {
            "row_id": ["a", "b", "c", "d"],
            "side": ["put", "put", "put", "call"],
            "delivery_date": ["2022-01-01", "2022-01-01", "2022-01-02", "2022-01-01"],
        }
    )

    weights = eq.side_day_weights(frame)

    assert weights.iloc[0] == pytest.approx(0.25)
    assert weights.iloc[1] == pytest.approx(0.25)
    assert weights.iloc[2] == pytest.approx(0.5)
    assert weights.iloc[3] == pytest.approx(1.0)
    assert weights.iloc[:3].sum() == pytest.approx(1.0)


def test_es95_keeps_uncapped_tail_above_one():
    assert eq.weighted_es95([0.0, 0.1, 1.8], [0.45, 0.45, 0.10]) == pytest.approx(1.8)


def test_fit_vol_median_rejects_missing_preceding_fit_window():
    source = pd.DataFrame(
        {
            "row_id": ["late"],
            "side": ["put"],
            "delivery_date": ["2021-12-15"],
            "as_of_ms": [eq.utc_ms(2021, 12, 15)],
            "expiry_ms": [eq.utc_ms(2021, 12, 16)],
            "vol_240": [0.2],
        }
    )

    with pytest.raises(ValueError, match="fit_vol_median_unavailable:2022:put_credit"):
        eq.compute_fit_vol_medians(source, [2022])


def test_fit_vol_median_uses_asof_and_expiry_not_delivery_date():
    source = pd.DataFrame(
        [
            {
                "row_id": "put-good",
                "side": "put",
                "delivery_date": "2022-01-01",
                "as_of_ms": eq.utc_ms(2020, 1, 1),
                "expiry_ms": eq.utc_ms(2020, 1, 2),
                "vol_240": 0.2,
            },
            {
                "row_id": "put-delivery-looks-ok-but-asof-late",
                "side": "put",
                "delivery_date": "2021-09-30",
                "as_of_ms": eq.utc_ms(2021, 10, 1),
                "expiry_ms": eq.utc_ms(2021, 10, 2),
                "vol_240": 9.9,
            },
            {
                "row_id": "put-expiry-on-cut",
                "side": "put",
                "delivery_date": "2021-09-30",
                "as_of_ms": eq.utc_ms(2021, 9, 30),
                "expiry_ms": eq.utc_ms(2021, 10, 1),
                "vol_240": 8.8,
            },
            {
                "row_id": "call-good",
                "side": "call",
                "delivery_date": "2021-09-30",
                "as_of_ms": eq.utc_ms(2021, 9, 30),
                "expiry_ms": eq.utc_ms(2021, 9, 30) + 1,
                "vol_240": 0.3,
            },
        ]
    )

    result = eq.compute_fit_vol_medians(source, [2022])

    assert result[(2022, "put_credit")] == pytest.approx(0.2)
    assert result[(2022, "call_credit")] == pytest.approx(0.3)


def test_missing_eval_vol_gets_own_stratum_not_low_or_zero():
    frame = _frame(1)
    frame["vol_240"] = [None]
    frame["delivery_date"] = ["2022-01-01"]

    out = eq.add_opportunity_strata(frame, _protocol(), {(2022, "put_credit"): 0.01})

    assert out.loc[0, "vol_fit_bin"] == "vol_missing"
    assert "vol=vol_missing" in out.loc[0, "stratum_key"]


def test_calendar_bootstrap_blocks_are_anchored_nonoverlap():
    blocks = eq.anchored_nonoverlap_blocks("1970-01-06", "1970-01-10", 7)

    assert blocks == [["1970-01-06", "1970-01-07"], ["1970-01-08", "1970-01-09", "1970-01-10"]]


def test_prediction_model_input_label_mismatch_is_rejected():
    pred = pd.DataFrame(
        {
            "row_id": ["r1"],
            "observation_id": ["obs"],
            "as_of_ms": [1],
            "entry_ms": [1],
            "expiry_ms": [2],
            "delivery_date": ["2022-01-01"],
            "side": ["put_credit"],
            "target_width": [2000.0],
            "actual_width": [2000.0],
            "expected_loss_normalized": [0.1],
            "probability_positive": [0.2],
            "breach_probability": [0.03],
            "actual_loss_normalized": [0.0],
            "actual_payout_btc": [0.0],
            "protection_breached": [False],
        }
    )
    inputs = pd.DataFrame(
        {
            "row_id": ["r1"],
            "observation_id": ["obs"],
            "as_of_ms": [1],
            "entry_ms": [1],
            "expiry_ms": [2],
            "delivery_date": ["2022-01-01"],
            "side": ["put"],
            "target_width": [2000.0],
            "actual_width": [2000.0],
            "entry_price": [50000.0],
            "dte_hours": [12.0],
            "short_distance_fraction": [0.01],
            "width_fraction": [0.04],
            "vol_240": [0.02],
            "loss_normalized": [0.1],
            "payout_btc": [0.0],
            "short_leg_breached": [False],
            "protection_leg_breached": [False],
        }
    )

    with pytest.raises(ValueError, match="label_mismatch:loss_normalized"):
        eq.merge_predictions_with_inputs({name: pred.copy() for name in eq.CANDIDATE_FILES}, inputs)


def test_statistical_membership_depends_on_score_not_actual_label():
    frame = _frame(21)
    frame["statistical_score"] = list(range(21))

    first, _ = eq.allocate_fractional_coverage(frame, "statistical_score", 0.5, min_stratum_days=20)
    changed = frame.copy()
    changed["actual_loss_normalized"] = list(reversed(range(21)))
    second, _ = eq.allocate_fractional_coverage(changed, "statistical_score", 0.5, min_stratum_days=20)

    assert first.tolist() == pytest.approx(second.tolist())


def test_fast_fractional_allocator_matches_slow_reference_with_ties():
    frame = _frame(8)
    frame["row_weight"] = [0.05, 0.1, 0.1, 0.25, 0.1, 0.05, 0.2, 0.15]
    frame["score"] = [0.3, 0.1, 0.1, 0.2, 0.3, 0.4, 0.4, 0.5]

    fast, _ = eq.allocate_fractional_coverage(frame, "score", 0.55, min_stratum_days=1)
    slow = pd.Series(0.0, index=frame.index)
    selected = 0.0
    target = 0.55 * frame["row_weight"].sum()
    for score in sorted(frame["score"].unique()):
        idx = frame.index[frame["score"] == score]
        group_weight = frame.loc[idx, "row_weight"].sum()
        remaining = target - selected
        fraction = 0.0 if remaining <= 0 else min(1.0, remaining / group_weight)
        slow.loc[idx] = fraction
        selected += group_weight * fraction

    assert fast.tolist() == pytest.approx(slow.tolist())


def test_bootstrap_and_reported_difference_use_sufficient_stats_not_daily_mean_average():
    rows = [
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 0.0, "selected_weight": 100.0},
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 100.0, "selected_weight": 1.0},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 1000.0, "selected_weight": 100.0},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 0.0, "selected_weight": 1.0},
    ]

    report = eq.block_bootstrap_stat_vs_geometry(rows, side="put_credit", coverage=0.5, repetitions=8, seed=1, block_days=7)

    assert report["mean_difference_stat_minus_geometry"] == pytest.approx((1000.0 / 101.0) - (100.0 / 101.0))
    assert report["mean_difference_stat_minus_geometry"] != pytest.approx(((10.0 - 0.0) + (0.0 - 100.0)) / 2.0)
    assert report["bootstrap_difference_center_97_5_ci"][0] <= report["bootstrap_difference_upper_98_75"]
    assert "bootstrap_difference_ci_95" not in report


def test_bootstrap_keeps_single_method_zero_selected_weight_calendar_days():
    rows = [
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 10.0, "selected_weight": 1.0},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 0.0, "selected_weight": 0.0},
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 0.0, "selected_weight": 0.0},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 0.0, "selected_weight": 1.0},
    ]

    report = eq.block_bootstrap_stat_vs_geometry(rows, side="put_credit", coverage=0.5, repetitions=4, seed=1, block_days=7)

    assert report["status"] == "available"
    assert report["days"] == 2
    assert report["mean_difference_stat_minus_geometry"] == pytest.approx(-10.0)


def test_top10_favorable_uses_leave_one_contribution_not_daily_mean_difference():
    rows = [
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 300.0, "selected_weight": 100.0},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-01", "selected_loss_sum": 100.0, "selected_weight": 100.0},
        {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 0.1, "selected_weight": 0.001},
        {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": "2022-01-02", "selected_loss_sum": 0.0, "selected_weight": 0.001},
    ]
    for day in range(3, 13):
        date = f"2022-01-{day:02d}"
        rows.extend(
            [
                {"method": "geometry", "coverage": 0.5, "side": "put_credit", "delivery_date": date, "selected_loss_sum": 1.0, "selected_weight": 1.0},
                {"method": "statistical", "coverage": 0.5, "side": "put_credit", "delivery_date": date, "selected_loss_sum": 1.0, "selected_weight": 1.0},
            ]
        )

    report = eq.block_bootstrap_stat_vs_geometry(rows, side="put_credit", coverage=0.5, repetitions=4, seed=1, block_days=7)

    assert report["top10_favorable_days_for_statistical"][0]["delivery_date"] == "2022-01-01"
    assert report["top10_favorable_days_for_statistical"][0]["leave_one_contribution_to_overall_difference"] < 0
    assert report["top10_favorable_days_for_statistical"][0]["daily_mean_difference_stat_minus_geometry"] == pytest.approx(-2.0)
    assert report["daily_leave_one_contributions"][1]["daily_mean_difference_stat_minus_geometry"] == pytest.approx(-100.0)
    assert report["leave_10_most_favorable_out"]["days"] == 2


def test_diagnostic_development_support_requires_all_gates():
    methods = {
        "statistical": {"by_side": {"put_credit": {"selected_es95_uncapped": 0.9, "informative_weight_fraction": 0.6}}},
        "geometry": {"by_side": {"put_credit": {"selected_es95_uncapped": 1.0}}},
    }
    comparison = {
        "bootstrap_difference_upper_98_75": -0.01,
        "leave_10_most_favorable_out": {"mean_difference_stat_minus_geometry": -0.02},
        "annual": {
            "2022": {"mean_difference_stat_minus_geometry": -0.01},
            "2023": {"mean_difference_stat_minus_geometry": 0.0},
            "2024": {"mean_difference_stat_minus_geometry": -0.03},
            "2025": {"mean_difference_stat_minus_geometry": 0.02},
        },
    }

    support = eq.diagnostic_development_support_for_side(methods, comparison, "put_credit")
    failed = eq.diagnostic_development_support_for_side(
        methods,
        {**comparison, "bootstrap_difference_upper_98_75": 0.01},
        "put_credit",
    )

    assert support["support"] is True
    assert support["production"] is False
    assert support["annual_nonworse_count"] == 3
    assert failed["support"] is False
