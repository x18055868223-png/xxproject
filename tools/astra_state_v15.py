"""Two-state volatility-ordered regime helpers for Astra v1.5 research.

Inputs are expected to be finite, already standardized FIT features:
[ret_30, log(vol_30 + 1e-8), net_flow_30].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.special import logsumexp


GAP_MS = 1_800_000
N_COMPONENTS = 2
N_FEATURES = 3
MODEL_VERSION = "astra_state_v15"


@dataclass(frozen=True)
class _Inputs:
    X: np.ndarray
    times_ms: np.ndarray
    lengths: list[int]


def _validate_inputs(X: np.ndarray, times_ms: np.ndarray) -> _Inputs:
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != N_FEATURES:
        raise ValueError("X must have shape (n, 3)")
    if arr.shape[0] == 0:
        raise ValueError("X must contain at least one row")
    if not np.isfinite(arr).all():
        raise ValueError("X must contain only finite values")

    times = np.asarray(times_ms)
    if times.ndim != 1 or times.shape[0] != arr.shape[0]:
        raise ValueError("times_ms must be a 1D array with the same length as X")
    times = times.astype(np.int64, copy=False)
    if len(np.unique(times)) != len(times):
        raise ValueError("Duplicate timestamp in times_ms")
    diffs = np.diff(times)
    if np.any(diffs <= 0):
        raise ValueError("times_ms must be strictly increasing")

    lengths: list[int] = []
    start = 0
    for i, diff in enumerate(diffs, start=1):
        if int(diff) != GAP_MS:
            lengths.append(i - start)
            start = i
    lengths.append(len(times) - start)
    return _Inputs(arr, times, lengths)


def _as_diag_covars(covars: Any) -> np.ndarray:
    cov = np.asarray(covars, dtype=float)
    if cov.ndim == 3:
        cov = np.stack([np.diag(c) for c in cov], axis=0)
    if cov.shape != (N_COMPONENTS, N_FEATURES):
        raise ValueError("Expected diagonal covariances with shape (2, 3)")
    return np.maximum(cov, 1e-12)


def _order_by_logvol(means: Any) -> np.ndarray:
    mean_arr = np.asarray(means, dtype=float)
    if mean_arr.shape != (N_COMPONENTS, N_FEATURES):
        raise ValueError("Expected means with shape (2, 3)")
    return np.argsort(mean_arr[:, 1], kind="stable")


def _reorder_gmm_in_place(model: Any) -> np.ndarray:
    order = _order_by_logvol(model.means_)
    model.weights_ = np.asarray(model.weights_, dtype=float)[order]
    model.means_ = np.asarray(model.means_, dtype=float)[order]
    model.covariances_ = _as_diag_covars(model.covariances_)[order]
    if hasattr(model, "precisions_cholesky_"):
        model.precisions_cholesky_ = np.asarray(model.precisions_cholesky_, dtype=float)[order]
    if hasattr(model, "precisions_"):
        model.precisions_ = np.asarray(model.precisions_, dtype=float)[order]
    return order


def _reorder_hmm_in_place(model: Any) -> np.ndarray:
    order = _order_by_logvol(model.means_)
    inv = np.empty_like(order)
    inv[order] = np.arange(len(order))
    model.startprob_ = np.asarray(model.startprob_, dtype=float)[order]
    model.transmat_ = np.asarray(model.transmat_, dtype=float)[np.ix_(order, order)]
    model.means_ = np.asarray(model.means_, dtype=float)[order]
    model.covars_ = _as_diag_covars(model.covars_)[order]
    return inv


def _gmm_params(model: Any, seed: int | None, loglik: float | None) -> dict[str, Any]:
    return {
        "seed": seed,
        "loglik": None if loglik is None else float(loglik),
        "weights": np.asarray(model.weights_, dtype=float).tolist(),
        "means": np.asarray(model.means_, dtype=float).tolist(),
        "covars": _as_diag_covars(model.covariances_).tolist(),
    }


def _hmm_params(model: Any, seed: int | None, loglik: float | None) -> dict[str, Any]:
    return {
        "seed": seed,
        "loglik": None if loglik is None else float(loglik),
        "startprob": np.asarray(model.startprob_, dtype=float).tolist(),
        "transmat": np.asarray(model.transmat_, dtype=float).tolist(),
        "means": np.asarray(model.means_, dtype=float).tolist(),
        "covars": _as_diag_covars(model.covars_).tolist(),
    }


def _diag_gaussian_logprob(X: np.ndarray, means: np.ndarray, covars: np.ndarray) -> np.ndarray:
    covars = np.maximum(np.asarray(covars, dtype=float), 1e-12)
    means = np.asarray(means, dtype=float)
    diff = X[:, None, :] - means[None, :, :]
    log_det = np.sum(np.log(covars), axis=1)
    maha = np.sum((diff * diff) / covars[None, :, :], axis=2)
    return -0.5 * (N_FEATURES * np.log(2.0 * np.pi) + log_det[None, :] + maha)


def _posterior_from_log_joint(log_joint: np.ndarray) -> np.ndarray:
    return np.exp(log_joint - logsumexp(log_joint, axis=1, keepdims=True))


def _stationary_prior(transmat: np.ndarray) -> np.ndarray:
    trans = np.asarray(transmat, dtype=float)
    a = np.vstack([(trans.T - np.eye(N_COMPONENTS))[:1], np.ones(N_COMPONENTS)])
    b = np.array([0.0, 1.0])
    prior = np.linalg.lstsq(a, b, rcond=None)[0]
    prior = np.clip(prior, 0.0, None)
    total = float(prior.sum())
    if total <= 0 or not np.isfinite(total):
        return np.full(N_COMPONENTS, 1.0 / N_COMPONENTS)
    return prior / total


def _normalize_prob_vector(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.shape != (N_COMPONENTS,) or not np.isfinite(arr).all() or np.any(arr < 0):
        raise ValueError(f"{name} must be a finite non-negative length-2 vector")
    total = float(arr.sum())
    if total <= 0:
        raise ValueError(f"{name} must have positive mass")
    return arr / total


def _normalize_transmat(values: Any) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.shape != (N_COMPONENTS, N_COMPONENTS) or not np.isfinite(arr).all() or np.any(arr < 0):
        raise ValueError("transmat must be a finite non-negative 2x2 matrix")
    row_sum = arr.sum(axis=1, keepdims=True)
    if np.any(row_sum <= 0):
        raise ValueError("transmat rows must have positive mass")
    return arr / row_sum


def _model_params(models: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "gmm" not in models or "hmm" not in models:
        raise ValueError("models must contain gmm and hmm entries")
    gmm = dict(models["gmm"])
    hmm = dict(models["hmm"])
    for key in ("weights", "means", "covars"):
        if key not in gmm:
            raise ValueError(f"gmm missing {key}")
    for key in ("startprob", "transmat", "means", "covars"):
        if key not in hmm:
            raise ValueError(f"hmm missing {key}")
    gmm["weights"] = _normalize_prob_vector(gmm["weights"], "gmm weights")
    gmm["means"] = np.asarray(gmm["means"], dtype=float)
    gmm["covars"] = _as_diag_covars(gmm["covars"])
    hmm["startprob"] = _normalize_prob_vector(hmm["startprob"], "hmm startprob")
    hmm["transmat"] = _normalize_transmat(hmm["transmat"])
    hmm["means"] = np.asarray(hmm["means"], dtype=float)
    hmm["covars"] = _as_diag_covars(hmm["covars"])
    return gmm, hmm


def _fit_candidate(X: np.ndarray, lengths: list[int], seed: int) -> tuple[Any, Any, dict[str, Any]]:
    from sklearn.mixture import GaussianMixture

    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent
        raise ImportError("hmmlearn is required for fit_state_models; filtering portable models does not require it") from exc

    gmm = GaussianMixture(
        n_components=N_COMPONENTS,
        covariance_type="diag",
        max_iter=200,
        tol=1e-3,
        reg_covar=1e-4,
        n_init=1,
        random_state=seed,
    )
    gmm.fit(X)
    gmm_loglik = float(np.sum(gmm.score_samples(X)))
    _reorder_gmm_in_place(gmm)

    hmm = GaussianHMM(
        n_components=N_COMPONENTS,
        covariance_type="diag",
        n_iter=200,
        tol=1e-3,
        min_covar=1e-4,
        random_state=seed,
        init_params="",
        params="stmc",
    )
    hmm.startprob_ = np.full(N_COMPONENTS, 1.0 / N_COMPONENTS)
    hmm.transmat_ = np.array([[0.9, 0.1], [0.1, 0.9]], dtype=float)
    hmm.means_ = np.asarray(gmm.means_, dtype=float).copy()
    init_covars = _as_diag_covars(gmm.covariances_).copy()
    hmm.covars_ = init_covars
    hmm_init = {
        "source": "same_seed_sorted_gmm",
        "startprob": np.asarray(hmm.startprob_, dtype=float).tolist(),
        "transmat": np.asarray(hmm.transmat_, dtype=float).tolist(),
        "means": np.asarray(hmm.means_, dtype=float).tolist(),
        "covars": init_covars.tolist(),
    }
    hmm.fit(X, lengths)
    hmm_loglik = float(hmm.score(X, lengths))
    _reorder_hmm_in_place(hmm)
    hmm_history = list(getattr(getattr(hmm, "monitor_", None), "history", []))
    hmm_final_delta = None
    if len(hmm_history) >= 2:
        hmm_final_delta = float(hmm_history[-1] - hmm_history[-2])
    gmm_n_iter = int(getattr(gmm, "n_iter_", -1))
    hmm_n_iter = int(getattr(getattr(hmm, "monitor_", None), "iter", -1))

    diag = {
        "seed": int(seed),
        "gmm_loglik": gmm_loglik,
        "gmm_converged": bool(getattr(gmm, "converged_", False)),
        "gmm_n_init": 1,
        "gmm_n_iter": gmm_n_iter,
        "gmm_hit_iter_limit": bool(gmm_n_iter >= 200),
        "gmm_final_delta": None,
        "gmm_final_delta_note": "sklearn GaussianMixture does not expose per-iteration EM history",
        "hmm_loglik": hmm_loglik,
        "hmm_converged": bool(getattr(getattr(hmm, "monitor_", None), "converged", False)),
        "hmm_n_iter": hmm_n_iter,
        "hmm_hit_iter_limit": bool(hmm_n_iter >= 200),
        "hmm_final_delta": hmm_final_delta,
        "hmm_initialization": hmm_init,
        "gmm_logvol_means": np.asarray(gmm.means_, dtype=float)[:, 1].tolist(),
        "hmm_logvol_means": np.asarray(hmm.means_, dtype=float)[:, 1].tolist(),
    }
    return gmm, hmm, diag


def fit_state_models(X: np.ndarray, times_ms: np.ndarray, seed: int = 20260922) -> dict[str, Any]:
    """Fit two-state GMM and HMM models from unlabeled historical rows."""
    inputs = _validate_inputs(X, times_ms)
    seeds = [int(seed) + offset for offset in range(3)]
    candidates: list[dict[str, Any]] = []
    fitted: list[tuple[Any, Any, dict[str, Any]]] = []
    for candidate_seed in seeds:
        gmm, hmm, diag = _fit_candidate(inputs.X, inputs.lengths, candidate_seed)
        candidates.append(diag)
        fitted.append((gmm, hmm, diag))

    gmm_idx = int(np.argmax([item[2]["gmm_loglik"] for item in fitted]))
    hmm_idx = int(np.argmax([item[2]["hmm_loglik"] for item in fitted]))
    best_gmm, _, gmm_diag = fitted[gmm_idx]
    _, best_hmm, hmm_diag = fitted[hmm_idx]

    metadata = {
        "version": MODEL_VERSION,
        "feature_order": ["ret_30", "log_vol_30", "net_flow_30"],
        "state_order": "ascending_feature_1_logvol_mean",
        "gap_ms": GAP_MS,
        "lengths": inputs.lengths,
        "seeds": seeds,
        "gmm_total_initializations": len(seeds),
        "candidates": candidates,
        "selected": {
            "gmm_seed": int(gmm_diag["seed"]),
            "gmm_loglik": float(gmm_diag["gmm_loglik"]),
            "hmm_seed": int(hmm_diag["seed"]),
            "hmm_loglik": float(hmm_diag["hmm_loglik"]),
        },
    }
    return {
        "version": MODEL_VERSION,
        "gap_ms": GAP_MS,
        "n_components": N_COMPONENTS,
        "n_features": N_FEATURES,
        "gmm": _gmm_params(best_gmm, int(gmm_diag["seed"]), float(gmm_diag["gmm_loglik"])),
        "hmm": _hmm_params(best_hmm, int(hmm_diag["seed"]), float(hmm_diag["hmm_loglik"])),
        "metadata": metadata,
        "gmm_estimator": best_gmm,
        "hmm_estimator": best_hmm,
    }


def portable_model(modeldict: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-serializable fitted parameters without estimator objects."""
    gmm, hmm = _model_params(modeldict)
    metadata = modeldict.get("metadata", {})
    return {
        "version": modeldict.get("version", MODEL_VERSION),
        "gap_ms": int(modeldict.get("gap_ms", GAP_MS)),
        "n_components": N_COMPONENTS,
        "n_features": N_FEATURES,
        "gmm": {
            "seed": modeldict.get("gmm", {}).get("seed"),
            "loglik": modeldict.get("gmm", {}).get("loglik"),
            "weights": gmm["weights"].tolist(),
            "means": gmm["means"].tolist(),
            "covars": gmm["covars"].tolist(),
        },
        "hmm": {
            "seed": modeldict.get("hmm", {}).get("seed"),
            "loglik": modeldict.get("hmm", {}).get("loglik"),
            "startprob": hmm["startprob"].tolist(),
            "transmat": hmm["transmat"].tolist(),
            "means": hmm["means"].tolist(),
            "covars": hmm["covars"].tolist(),
        },
        "metadata": _jsonable(metadata),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def filter_states(models: dict[str, Any], X: np.ndarray, times_ms: np.ndarray) -> dict[str, np.ndarray]:
    """Filter current state probabilities online, resetting HMM memory at gaps."""
    inputs = _validate_inputs(X, times_ms)
    gmm, hmm = _model_params(models)

    mix_log_joint = np.log(gmm["weights"])[None, :] + _diag_gaussian_logprob(inputs.X, gmm["means"], gmm["covars"])
    mix_post = _posterior_from_log_joint(mix_log_joint)

    hmm_emission = _diag_gaussian_logprob(inputs.X, hmm["means"], hmm["covars"])
    trans_log = np.log(np.maximum(hmm["transmat"], 1e-300))
    start_log = np.log(np.maximum(hmm["startprob"], 1e-300))
    reset_prior = _stationary_prior(hmm["transmat"])
    reset_log = np.log(np.maximum(reset_prior, 1e-300))

    hmm_p1 = np.empty(inputs.X.shape[0], dtype=float)
    hmm_reset_p1 = np.empty(inputs.X.shape[0], dtype=float)
    log_alpha: np.ndarray | None = None
    for row in range(inputs.X.shape[0]):
        if row == 0 or int(inputs.times_ms[row] - inputs.times_ms[row - 1]) != GAP_MS:
            log_prior = start_log
        else:
            if log_alpha is None:
                raise RuntimeError("internal HMM filter state was not initialized")
            log_prior = logsumexp(log_alpha[:, None] + trans_log, axis=0)
        log_alpha = log_prior + hmm_emission[row]
        log_alpha = log_alpha - logsumexp(log_alpha)
        hmm_p1[row] = float(np.exp(log_alpha[1]))

        reset_alpha = reset_log + hmm_emission[row]
        reset_alpha = reset_alpha - logsumexp(reset_alpha)
        hmm_reset_p1[row] = float(np.exp(reset_alpha[1]))

    return {
        "mix_p1": mix_post[:, 1],
        "hmm_p1": hmm_p1,
        "hmm_reset_p1": hmm_reset_p1,
    }


def dynamic_features(X: np.ndarray, times_ms: np.ndarray) -> np.ndarray:
    """Return lag-1, EWM span-8 and EWM span-48 features with gap resets."""
    inputs = _validate_inputs(X, times_ms)
    out = np.empty((inputs.X.shape[0], N_FEATURES * 3), dtype=float)
    ewm8 = np.empty(N_FEATURES, dtype=float)
    ewm48 = np.empty(N_FEATURES, dtype=float)
    alpha8 = 2.0 / (8.0 + 1.0)
    alpha48 = 2.0 / (48.0 + 1.0)
    for row, values in enumerate(inputs.X):
        is_new_segment = row == 0 or int(inputs.times_ms[row] - inputs.times_ms[row - 1]) != GAP_MS
        if is_new_segment:
            lag = values
            ewm8 = values.copy()
            ewm48 = values.copy()
        else:
            lag = inputs.X[row - 1]
            ewm8 = alpha8 * values + (1.0 - alpha8) * ewm8
            ewm48 = alpha48 * values + (1.0 - alpha48) * ewm48
        out[row] = np.concatenate([lag, ewm8, ewm48])
    return out


__all__ = ["fit_state_models", "portable_model", "filter_states", "dynamic_features"]
