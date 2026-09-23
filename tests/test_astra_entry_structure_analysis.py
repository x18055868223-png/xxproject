from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import astra_entry_structure_analysis as a


PROTOCOL = {"structure": {"fixed_dte_edges": [8., 16., 24.],
                           "fixed_distance_to_width_edges": [.25, .5, 1., 2.],
                           "fixed_shift_to_width_edges": [.25, .5, 1., 2.],
                           "fixed_width_fraction_edges": [.02, .05, .1]}}


def training_frame(days=60):
    rows = []
    for i, day in enumerate(pd.date_range("2020-01-01", periods=days)):
        for vol, label in ((.01, .1), (.03, .3)):
            rows.append(dict(side="put", delivery_date=day.strftime("%Y-%m-%d"),
                             dte_hours=12., distance_to_width=.2, shift_to_width=.25,
                             width_fraction=.04, vol_240=vol, delta_normalized=label))
    return pd.DataFrame(rows)


def test_duplicate_complete_day_does_not_change_fit_or_cut():
    frame = training_frame()
    repeated = pd.concat([frame, frame[frame.delivery_date == "2020-01-01"]] * 2, ignore_index=True)
    first, second = a.fit_tables(frame, PROTOCOL), a.fit_tables(repeated, PROTOCOL)
    assert first == second
    assert first["global_mean"] == pytest.approx(.2)
    assert first["volatility_cut"] == .01


def test_cell_shrinkage_and_missing_optional_vol_fallback():
    fit = training_frame()
    model = a.fit_tables(fit, PROTOCOL)
    ev = fit.iloc[:2].copy()
    ev.loc[ev.index[1], "vol_240"] = np.nan
    predicted = a.predict_tables(ev, model, PROTOCOL)
    assert predicted[a.MODELS[1]].tolist() == pytest.approx([.2, .2])
    assert predicted[a.MODELS[2]].tolist() == pytest.approx([(.1 * 60 + .2 * 30)/90, .2])
    assert predicted[a.MODELS[2] + "_supported"].tolist() == [True, False]


def test_sparse_cells_fallback_without_dropping_observations():
    frame = training_frame(days=29)
    model = a.fit_tables(frame, PROTOCOL)
    assert model["geometry"] == {}
    assert model["geometry_vol"] == {}
    predicted = a.predict_tables(frame, model, PROTOCOL)
    assert len(predicted) == len(frame)
    assert np.allclose(predicted[a.MODELS[2]], .2)


def test_prediction_does_not_use_evaluation_labels():
    frame = training_frame()
    model = a.fit_tables(frame, PROTOCOL)
    changed = frame.copy()
    changed["delta_normalized"] = 999.
    p = a.predict_tables(frame, model, PROTOCOL)
    q = a.predict_tables(changed, model, PROTOCOL)
    assert p[list(a.MODELS)].equals(q[list(a.MODELS)])


def test_fractional_es_keeps_inverse_payout_above_one():
    assert a.es95([0., 2.], [.99, .01]) == pytest.approx(.4)
    assert a.es95([2., 3.], [1., 1.]) == pytest.approx(3.)


def test_fixed_original_numeraire_preserves_pair_difference():
    f = pd.DataFrame([dict(entry_price=100., short_strike=99., outward_short_strike=98.,
                           actual_width=2., dte_hours=12., delta_normalized=.4)])
    out = a.prepare_features(f)
    assert out.distance_to_width.iloc[0] == .5
    assert out.shift_to_width.iloc[0] == .5
    assert out.width_fraction.iloc[0] == .02
    with pytest.raises(ValueError, match="monotonicity"):
        a.prepare_features(f.assign(delta_normalized=-.01))


def test_split_purges_expiry_at_boundary():
    def stamp(s):
        return pd.Timestamp(s, tz="UTC").timestamp() * 1000
    frame = pd.DataFrame([
        dict(as_of_ms=stamp("2021-09-30"), expiry_ms=stamp("2021-10-01"), delivery_date="2021-10-01"),
        dict(as_of_ms=stamp("2021-09-29"), expiry_ms=stamp("2021-09-30"), delivery_date="2021-09-30"),
        dict(as_of_ms=stamp("2021-10-01"), expiry_ms=stamp("2021-10-02"), delivery_date="2021-10-02"),
        dict(as_of_ms=stamp("2022-01-01"), expiry_ms=stamp("2022-01-02"), delivery_date="2022-01-02"),
    ])
    (fit, cal, ev), meta = a.split_year(frame, 2022)
    assert [len(fit), len(cal), len(ev)] == [1, 1, 1]
    assert meta["purged_cross_boundary_rows"] == [1, 0, 0]


def test_bootstrap_constant_improvement_sign_and_reproducibility():
    days = pd.date_range("2022-01-01", periods=100).strftime("%Y-%m-%d")
    daily = pd.Series(-.02, index=days)
    result = a.calendar_bootstrap_daily(daily, .9875, reps=100)
    assert result["upper"] == pytest.approx(-.02)
    assert result == a.calendar_bootstrap_daily(daily, .9875, reps=100)


def test_identical_scores_half_selection_is_uniform_not_hindsight():
    f = training_frame()
    f["constant"] = 0.
    result = a.high_saving_half(f, "constant")
    assert result["coverage"] == pytest.approx(.5)
    assert result["selected_delta"] == pytest.approx(result["remaining_delta"])


def parity_fixture():
    original = pd.DataFrame([dict(row_id="x", side="put", delivery_date="2022-01-02", short_name="A100", long_name="A90",
        as_of_ms=10, entry_ms=10, expiry_ms=20, actual_width=10., entry_price=101., short_strike=100., long_strike=90.,
        short_creation_ms=1, long_creation_ms=1, settlement_price=92., payout_btc=8/92, loss_normalized=(8/92)/(10/101))])
    pair = original.copy()
    pair["outward_short_strike"] = 95.
    pair["outward_long_strike"] = 85.
    pair["outward_short_creation_ms"] = 1
    pair["outward_long_creation_ms"] = 1
    pair["outward_payout_btc"] = 3/92
    pair["outward_loss_normalized"] = (3/92)/(10/101)
    pair["delta_btc"] = 5/92
    pair["delta_normalized"] = (5/92)/(10/101)
    return pair, original, {"2022-01-02": 92.}


def test_independent_ledger_parity_checks_full_cashflows():
    pair, original, prices = parity_fixture()
    result = a.validate_ledger(pair, original, prices)
    assert result["rows"] == 1
    assert max(result["maximum_absolute_errors"].values()) < 1e-12


@pytest.mark.parametrize("column,value", [("outward_long_strike", 84.), ("delta_btc", .123),
                                         ("delta_normalized", .123), ("loss_normalized", .123),
                                         ("outward_short_creation_ms", 11)])
def test_corrupt_pairs_fail_before_modeling(column, value):
    pair, original, prices = parity_fixture()
    pair[column] = value
    with pytest.raises(ValueError):
        a.validate_ledger(pair, original, prices)


def test_official_delivery_mismatch_refuses_analysis():
    pair, original, prices = parity_fixture()
    prices["2022-01-02"] = 93.
    with pytest.raises(ValueError, match="Official"):
        a.validate_ledger(pair, original, prices)
