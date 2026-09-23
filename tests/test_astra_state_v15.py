import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from astra_state_v15 import dynamic_features, filter_states, fit_state_models, portable_model


GAP = 1_800_000


def manual_models():
    return {
        "gmm": {
            "weights": [0.5, 0.5],
            "means": [[-1.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
            "covars": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        },
        "hmm": {
            "startprob": [0.5, 0.5],
            "transmat": [[0.8, 0.2], [0.1, 0.9]],
            "means": [[-1.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
            "covars": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        },
    }


def normal_emission(x, mean, var):
    diff = np.asarray(x) - np.asarray(mean)
    var = np.asarray(var)
    return math.exp(-0.5 * (3 * math.log(2 * math.pi) + float(np.log(var).sum()) + float(((diff * diff) / var).sum())))


def normalize(values):
    values = np.asarray(values, dtype=float)
    return values / values.sum()


def test_manual_two_state_bayes_one_and_two_steps():
    models = manual_models()
    X = np.array([[0.6, 0.8, 0.0], [-0.7, 0.1, 0.0]])
    times = np.array([0, GAP])
    got = filter_states(models, X, times)

    means = models["hmm"]["means"]
    covars = models["hmm"]["covars"]
    emit0 = np.array([normal_emission(X[0], means[0], covars[0]), normal_emission(X[0], means[1], covars[1])])
    alpha0 = normalize(np.array(models["hmm"]["startprob"]) * emit0)
    emit1 = np.array([normal_emission(X[1], means[0], covars[0]), normal_emission(X[1], means[1], covars[1])])
    alpha1 = normalize(alpha0 @ np.array(models["hmm"]["transmat"]) * emit1)

    assert got["hmm_p1"][0] == pytest.approx(alpha0[1])
    assert got["hmm_p1"][1] == pytest.approx(alpha1[1])


def test_future_append_does_not_change_prefix_filter_values():
    models = manual_models()
    base_X = np.array([[0.7, 0.8, 0.0], [0.5, 0.7, 0.0], [-0.7, 0.0, 0.0]])
    base_t = np.array([0, GAP, GAP * 2])
    extended_X = np.vstack([base_X, [[1.5, 1.3, 0.0], [-2.0, -1.0, 0.0]]])
    extended_t = np.array([0, GAP, GAP * 2, GAP * 3, GAP * 4])

    base = filter_states(models, base_X, base_t)
    extended = filter_states(models, extended_X, extended_t)

    for key in ("mix_p1", "hmm_p1", "hmm_reset_p1"):
        np.testing.assert_allclose(base[key], extended[key][: len(base_X)])


def test_gap_resets_hmm_memory_to_start_prior():
    models = manual_models()
    X = np.array([[2.0, 2.0, 0.0], [2.0, 2.0, 0.0], [-0.4, 0.2, 0.0]])
    times = np.array([0, GAP, GAP * 8])
    got = filter_states(models, X, times)

    reset_only = filter_states(models, X[2:], np.array([times[2]]))
    assert got["hmm_p1"][2] == pytest.approx(reset_only["hmm_p1"][0])


def test_duplicate_timestamp_is_rejected():
    models = manual_models()
    X = np.zeros((2, 3))
    times = np.array([0, 0])
    with pytest.raises(ValueError, match="Duplicate timestamp"):
        filter_states(models, X, times)
    with pytest.raises(ValueError, match="Duplicate timestamp"):
        dynamic_features(X, times)


def test_extreme_values_remain_finite_probabilities():
    models = manual_models()
    X = np.array([[1e8, 1e8, -1e8], [-1e8, -1e8, 1e8]])
    times = np.array([0, GAP])
    got = filter_states(models, X, times)

    for key, values in got.items():
        assert np.isfinite(values).all(), key
        assert ((values >= 0.0) & (values <= 1.0)).all(), key
        np.testing.assert_allclose(values + (1.0 - values), np.ones_like(values))


def test_dynamic_features_reset_lag_and_ewm_at_gap():
    X = np.array(
        [
            [1.0, 10.0, 100.0],
            [2.0, 20.0, 200.0],
            [9.0, 90.0, 900.0],
        ]
    )
    times = np.array([0, GAP, GAP * 5])
    got = dynamic_features(X, times)

    alpha8 = 2.0 / 9.0
    alpha48 = 2.0 / 49.0
    np.testing.assert_allclose(got[0], np.concatenate([X[0], X[0], X[0]]))
    np.testing.assert_allclose(got[1, :3], X[0])
    np.testing.assert_allclose(got[1, 3:6], alpha8 * X[1] + (1 - alpha8) * X[0])
    np.testing.assert_allclose(got[1, 6:9], alpha48 * X[1] + (1 - alpha48) * X[0])
    np.testing.assert_allclose(got[2], np.concatenate([X[2], X[2], X[2]]))


def test_fit_portable_export_reproduces_filtering_on_synthetic_hmm():
    pytest.importorskip("hmmlearn")
    rng = np.random.default_rng(20260922)
    means = np.array([[-1.2, -1.0, -0.4], [1.3, 1.2, 0.6]])
    covars = np.array([[0.12, 0.10, 0.15], [0.14, 0.12, 0.16]])
    trans = np.array([[0.92, 0.08], [0.12, 0.88]])
    state = 0
    rows = []
    for _ in range(220):
        state = int(rng.choice([0, 1], p=trans[state]))
        rows.append(rng.normal(means[state], np.sqrt(covars[state])))
    X = np.asarray(rows)
    times = np.arange(len(X), dtype=np.int64) * GAP

    fit = fit_state_models(X, times, seed=20260922)
    portable = portable_model(fit)
    json.dumps(portable, allow_nan=False)

    assert len(fit["metadata"]["candidates"]) == 3
    assert fit["metadata"]["gmm_total_initializations"] == 3
    assert [item["gmm_n_init"] for item in fit["metadata"]["candidates"]] == [1, 1, 1]
    assert all("hmm_final_delta" in item and "hmm_hit_iter_limit" in item for item in fit["metadata"]["candidates"])
    assert all("gmm_hit_iter_limit" in item for item in fit["metadata"]["candidates"])
    assert fit["gmm"]["means"][0][1] <= fit["gmm"]["means"][1][1]
    assert fit["hmm"]["means"][0][1] <= fit["hmm"]["means"][1][1]

    direct = filter_states(fit, X[:25], times[:25])
    restored = filter_states(portable, X[:25], times[:25])
    for key in ("mix_p1", "hmm_p1", "hmm_reset_p1"):
        np.testing.assert_allclose(direct[key], restored[key], atol=1e-12)
