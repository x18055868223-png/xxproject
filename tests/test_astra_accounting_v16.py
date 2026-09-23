import numpy as np
import pandas as pd
import pytest

from tools import astra_accounting_v16 as a


def test_payment_tail_can_improve_while_full_net_tail_worsens():
    payment_hold = np.array([10.0, 9.0])
    payment_exit = np.array([9.0, 9.5])
    credit = np.array([9.0, 0.0])
    net_hold = a.apply_net_loss(payment_hold, credit, 0.0)
    net_exit = a.apply_net_loss(payment_exit, credit, 0.0)
    assert a.base.weighted_es(payment_exit, [1, 1]) < a.base.weighted_es(payment_hold, [1, 1])
    assert a.base.weighted_es(net_exit, [1, 1]) > a.base.weighted_es(net_hold, [1, 1])


def test_mean_deltas_are_invariant_to_shared_entry_credit_and_cost():
    left_payment = np.array([0.0, 0.4, 1.2])
    right_payment = np.array([0.1, 0.3, 0.8])
    credit = np.array([0.2, 0.5, 0.1])
    w = np.array([1.0, 2.0, 3.0])
    left_net = a.apply_net_loss(left_payment, credit, 0.025)
    right_net = a.apply_net_loss(right_payment, credit, 0.025)
    assert a.weighted_mean(left_payment - right_payment, w) == pytest.approx(a.weighted_mean(left_net - right_net, w))


def test_structure_credit_concession_cashflow_identity_and_missing_pair_fallback():
    original_loss = np.array([0.50, 0.20, 0.70])
    outward_loss = np.array([0.10, 0.10, np.nan])
    orig_credit = np.array([0.18, 0.10, 0.20])
    out_credit = np.array([0.08, 0.05, np.nan])
    cost = 0.025
    actual_saving = original_loss - outward_loss
    q = orig_credit - out_credit
    original_net = original_loss - orig_credit + cost
    outward_net = outward_loss - out_credit + cost
    pair_available = np.array([True, True, False])
    action, gain, net = a.structure_policy_arrays(
        np.array([0.30, 0.04, 9.00]),
        actual_saving,
        q,
        original_net,
        outward_net,
        pair_available,
    )
    assert action.tolist() == [True, False, False]
    assert gain.tolist() == pytest.approx([(0.50 - 0.10) - (0.18 - 0.08), 0.0, 0.0], nan_ok=True)
    assert net.tolist() == pytest.approx([outward_net[0], original_net[1], original_net[2]], nan_ok=True)
    assert (original_net[:2] - outward_net[:2]).tolist() == pytest.approx((actual_saving[:2] - q[:2]).tolist())


def test_daily_portfolio_marks_partial_unknown_day_unknown():
    f = pd.DataFrame(
        {
            "delivery_date": ["2022-01-01", "2022-01-01", "2022-01-02"],
            "original_weight": [0.5, 0.5, 1.0],
        }
    )
    daily = a.daily_portfolio(f, np.array([1.0, np.nan, 2.0]))
    assert np.isnan(daily.loc["2022-01-01"])
    assert daily.loc["2022-01-02"] == pytest.approx(2.0)


def test_structure_identity_validator_rejects_side_mismatch():
    f = pd.DataFrame(
        {
            "side": ["put"],
            "side_pair": ["call"],
            "delivery_date": ["2022-01-01"],
            "delivery_date_pair": ["2022-01-01"],
            "as_of_ms": [1],
            "as_of_ms_pair": [1],
            "entry_ms": [1],
            "entry_ms_pair": [1],
            "expiry_ms": [2],
            "expiry_ms_pair": [2],
            "actual_width": [100.0],
            "actual_width_pair": [100.0],
            "entry_price": [1000.0],
            "entry_price_pair": [1000.0],
            "short_strike": [900.0],
            "short_strike_pair": [900.0],
            "long_strike": [800.0],
            "long_strike_pair": [800.0],
            "loss_normalized": [0.0],
            "loss_normalized_pair": [0.0],
        }
    )
    with pytest.raises(ValueError, match="side_pair"):
        a.validate_structure_identity(f)
