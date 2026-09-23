import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from astra_joint_v12_diagnostics import CANDIDATES, build_pairs, day_mean, summarize_pairs, weighted_es


def test_date_equal_loss_and_unclipped_coin_tail():
    frame = pd.DataFrame({"delivery_date": ["2022-01-01"] * 9 + ["2022-01-02"]})
    values = [0.] * 9 + [1.8]
    assert day_mean(frame, values) == pytest.approx(.9)
    assert weighted_es(frame, values) == pytest.approx(1.8)


def test_better_level_mse_can_have_worse_decisions_and_identity_decomposes():
    frame = pd.DataFrame({
        "delivery_date": ["2022-01-01", "2022-01-02"],
        "actual_put": [0., 1.2], "actual_call": [1., 0.],
        "geometry_put": [.8, 2.], "geometry_call": [1.8, .8],
        "statistical_put": [.6, .5], "statistical_call": [.4, .7],
        "joint_put": [.6, .5], "joint_call": [.4, .7],
    })
    report = summarize_pairs(frame)
    assert report["statistical"]["paired_row_mse"] < report["geometry"]["paired_row_mse"]
    assert report["statistical"]["selected_actual_loss"] > report["geometry"]["selected_actual_loss"]
    for group in CANDIDATES:
        item = report[group]
        assert item["paired_row_mse"] == pytest.approx(item["pair_center_mse"] + item["pair_delta_mse"] / 4)
    assert report["statistical_vs_geometry"]["harmful_flips"] == 2
    assert report["statistical_vs_geometry"]["mean_added_loss"] == pytest.approx(1.1)


def test_outcome_ties_are_not_claimed_as_correct_side_information():
    frame = pd.DataFrame({"delivery_date": ["2022-01-01"], "actual_put": [0.], "actual_call": [0.]})
    for group in CANDIDATES:
        frame[f"{group}_put"] = .1
        frame[f"{group}_call"] = .1
    report = summarize_pairs(frame)
    assert report["both_sides_equal_fraction"] == 1
    assert report["statistical"]["correct_on_unequal_actual"] is None
    assert report["statistical"]["predicted_ties"] == 1


def test_same_observation_id_cannot_mask_mismatched_entry_clock():
    records = [dict(row_id=side, observation_id="same", side=side + "_credit", delivery_date="2022-01-02",
                    as_of_ms=100, entry_ms=100 + index, expiry_ms=200, actual_width=2000,
                    actual_loss_normalized=0., expected_loss_normalized=.2, probability_positive=.3)
               for index, side in enumerate(("put", "call"))]
    frame = pd.DataFrame(records).set_index("row_id")
    with pytest.raises(ValueError, match="observation/entry time mismatch"):
        build_pairs({group: frame for group in CANDIDATES})
