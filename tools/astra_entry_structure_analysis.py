"""Sealed, local-only conditional same-side payout-difference research.

No option prices, economic EV, production model or tail probabilities are made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import traceback

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_MAX_THREADS"):
    os.environ[_name] = "6"

import numpy as np
import pandas as pd

MODELS = ("HIST_SIDE_MEAN", "GEOMETRY_TABLE", "GEOMETRY_VOL_TABLE")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def day_weights(frame):
    return (1.0 / frame.groupby("delivery_date")["delivery_date"].transform("size")).to_numpy()


def mean(x, weights):
    return float(np.average(np.asarray(x, float), weights=weights)) if len(x) and sum(weights) > 0 else None


def es95(x, weights):
    x, weights = np.asarray(x, float), np.asarray(weights, float)
    if not len(x) or weights.sum() <= 0:
        return None
    ix = np.argsort(-x, kind="stable")
    v, w = x[ix], weights[ix] / weights.sum()
    taken = np.minimum(w, np.maximum(0.0, .05 - (np.cumsum(w) - w)))
    return float(np.dot(v, taken) / .05)


def weighted_median(values, weights):
    x, w = np.asarray(values, float), np.asarray(weights, float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if not valid.any():
        raise ValueError("No finite training-only volatility values")
    x, w = x[valid], w[valid]
    order = np.argsort(x, kind="stable")
    x, w = x[order], w[order]
    return float(x[np.searchsorted(np.cumsum(w), w.sum() / 2, side="left")])


def bin_value(value, edges):
    if not np.isfinite(value):
        raise ValueError("Nonfinite geometric feature")
    return int(np.searchsorted(edges, value, side="right"))


def geometry_keys(frame, protocol):
    cfg = protocol["structure"]
    values = zip(frame.dte_hours, frame.distance_to_width, frame.shift_to_width, frame.width_fraction)
    return ["|".join(str(bin_value(v, e)) for v, e in zip(row, (
        cfg["fixed_dte_edges"], cfg["fixed_distance_to_width_edges"], cfg["fixed_shift_to_width_edges"], cfg["fixed_width_fraction_edges"]
    ))) for row in values]


def prepare_features(frame):
    out = frame.copy()
    out["distance_to_width"] = abs(out.entry_price - out.short_strike) / out.actual_width
    out["shift_to_width"] = abs(out.outward_short_strike - out.short_strike) / out.actual_width
    out["width_fraction"] = out.actual_width / out.entry_price
    needed = ["distance_to_width", "shift_to_width", "dte_hours", "actual_width", "entry_price", "delta_normalized"]
    if not np.isfinite(out[needed].to_numpy(float)).all():
        raise ValueError("Nonfinite mandatory pair feature/label")
    if (out.actual_width <= 0).any() or (out.entry_price <= 0).any() or (out.shift_to_width <= 0).any():
        raise ValueError("Invalid outward pair geometry")
    if (out.delta_normalized < -1e-12).any():
        raise ValueError("Outward payoff monotonicity violated; no clipping allowed")
    return out


def validate_ledger(frame, original, deliveries, instruments=None):
    """Independent vectorized payout and source parity, before any fitting."""
    if frame.row_id.duplicated().any() or original.row_id.duplicated().any():
        raise ValueError("Duplicate source/paired identity")
    if not frame.row_id.isin(original.row_id).all():
        raise ValueError("Paired identity absent from source")
    left = frame.reset_index(drop=True)
    source = original.set_index("row_id").loc[left.row_id].reset_index()
    for col in ["side", "delivery_date", "short_name", "long_name"]:
        if not np.array_equal(left[col].to_numpy(), source[col].to_numpy()):
            raise ValueError(f"Original identity parity: {col}")
    for col in ["as_of_ms", "entry_ms", "expiry_ms", "actual_width", "entry_price", "short_strike", "long_strike",
                "short_creation_ms", "long_creation_ms", "settlement_price", "payout_btc", "loss_normalized"]:
        if not np.allclose(left[col], source[col], rtol=0, atol=1e-9, equal_nan=False):
            raise ValueError(f"Original numerical parity: {col}")
    put = left.side.eq("put").to_numpy()
    if not left.side.isin(["put", "call"]).all():
        raise ValueError("Unknown side")
    w, s0 = left.actual_width.to_numpy(float), left.entry_price.to_numpy(float)
    s = left.delivery_date.map(deliveries).to_numpy(float)
    k, l = left.short_strike.to_numpy(float), left.long_strike.to_numpy(float)
    ko, lo = left.outward_short_strike.to_numpy(float), left.outward_long_strike.to_numpy(float)
    if not np.isfinite(s).all() or (s <= 0).any() or (w <= 0).any() or (s0 <= 0).any():
        raise ValueError("Missing/nonpositive official settlement or numeraire")
    if not np.allclose(s, left.settlement_price, rtol=0, atol=1e-9):
        raise ValueError("Official delivery mismatch")
    if not np.allclose(np.where(put, k-l, l-k), w, rtol=0, atol=1e-9) or not np.allclose(np.where(put, ko-lo, lo-ko), w, rtol=0, atol=1e-9):
        raise ValueError("Exact equal width violated")
    if not np.where(put, ko < k, ko > k).all() or not np.where(put, k < s0, k > s0).all():
        raise ValueError("Strictly outward/OTM geometry violated")
    p = np.where(put, np.maximum(k-s, 0)-np.maximum(l-s, 0), np.maximum(s-k, 0)-np.maximum(s-l, 0)) / s
    po = np.where(put, np.maximum(ko-s, 0)-np.maximum(lo-s, 0), np.maximum(s-ko, 0)-np.maximum(s-lo, 0)) / s
    expected = {"payout_btc": p, "outward_payout_btc": po, "delta_btc": p-po,
                "loss_normalized": p/(w/s0), "outward_loss_normalized": po/(w/s0), "delta_normalized": (p-po)/(w/s0)}
    errors = {}
    for col, values in expected.items():
        error = np.abs(left[col].to_numpy(float) - values)
        if not np.isfinite(error).all() or (error > 1e-9).any():
            raise ValueError(f"Independent payoff/numeraire parity: {col}")
        errors[col] = float(error.max()) if len(error) else 0.
    for prefix in ("short", "long", "outward_short", "outward_long"):
        if (left[prefix + "_creation_ms"] > left.entry_ms).any():
            raise ValueError("Future contract creation")
    if (left.entry_ms < left.as_of_ms).any() or (left.expiry_ms <= left.entry_ms).any():
        raise ValueError("Invalid as-of/entry/expiry ordering")
    if instruments is not None:
        by_name = {x["instrument_name"]: x for x in instruments}
        original_names = left.short_name
        attributes = ["kind", "option_type", "base_currency", "quote_currency", "counter_currency", "settlement_currency", "price_index", "contract_size"]
        for prefix in ("short", "long", "outward_short", "outward_long"):
            names = left[prefix + "_name"]
            if not names.isin(by_name).all():
                raise ValueError("Paired contract absent from archived metadata")
            for field, col in [("strike", prefix + "_strike"), ("creation_timestamp", prefix + "_creation_ms"), ("expiration_timestamp", "expiry_ms")]:
                observed = names.map({n: x[field] for n, x in by_name.items()}).to_numpy(float)
                if not np.allclose(observed, left[col], rtol=0, atol=1e-9):
                    raise ValueError(f"Contract metadata parity: {prefix}/{field}")
            for attr in attributes:
                mapping = {n: x.get(attr) for n, x in by_name.items()}
                observed, expected_attr = names.map(mapping), original_names.map(mapping)
                if observed.isna().any() or not np.array_equal(observed.to_numpy(), expected_attr.to_numpy()):
                    raise ValueError(f"Cross-leg contract attributes: {attr}")
    return {"rows": len(left), "maximum_absolute_errors": errors, "contracts_checked": instruments is not None,
            "official_delivery_checked": True, "full_tails_uncapped": True}


def split_period(frame, start, end):
    a = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    b = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    mask = (frame.as_of_ms >= a) & (frame.as_of_ms < b)
    return frame.loc[mask & (frame.expiry_ms < b)].copy(), int((mask & (frame.expiry_ms >= b)).sum())


def split_year(frame, year):
    limits = [(f"{year-2}-01-01", f"{year-1}-10-01"),
              (f"{year-1}-10-01", f"{year}-01-01"), (f"{year}-01-01", f"{year+1}-01-01")]
    parts, purged = [], []
    for start, end in limits:
        part, count = split_period(frame, start, end)
        parts.append(part)
        purged.append(count)
    for i in range(3):
        for j in range(i + 1, 3):
            if set(parts[i].delivery_date) & set(parts[j].delivery_date):
                raise ValueError("Delivery-day leakage across fit/cal/evaluation")
    return parts, {"windows": limits, "purged_cross_boundary_rows": purged,
                   "rows": [len(p) for p in parts], "delivery_days": [int(p.delivery_date.nunique()) for p in parts]}


def fit_tables(fit, protocol):
    """One side only; conditional day means with fixed shrinkage, no search."""
    if fit.empty or fit.side.nunique() != 1:
        raise ValueError("Fit needs one nonempty side")
    fit = fit.copy()
    global_mean = float(fit.groupby("delivery_date").delta_normalized.mean().mean())
    cut = weighted_median(fit.vol_240, day_weights(fit))
    fit["geometry_key"] = geometry_keys(fit, protocol)
    fit["vol_bin"] = np.where(fit.vol_240.isna(), "missing", np.where(fit.vol_240 <= cut, "low", "high"))
    fit["vol_key"] = fit.geometry_key + "|" + fit.vol_bin
    model = {"global_mean": global_mean, "volatility_cut": cut, "prior_days": 30,
             "minimum_cell_days": 30, "geometry": {}, "geometry_vol": {}}
    for key, group in fit.groupby("geometry_key", sort=True):
        days = group.groupby("delivery_date").delta_normalized.mean()
        if len(days) >= 30:
            model["geometry"][key] = {"prediction": float((days.sum() + 30 * global_mean) / (len(days) + 30)), "days": len(days)}
    for key, group in fit.groupby("vol_key", sort=True):
        if key.endswith("|missing"):
            continue  # optional volatility missing falls back; never interpreted as low
        days = group.groupby("delivery_date").delta_normalized.mean()
        parent = model["geometry"].get(key.rsplit("|", 1)[0], {}).get("prediction", global_mean)
        if len(days) >= 30:
            model["geometry_vol"][key] = {"prediction": float((days.sum() + 30 * parent) / (len(days) + 30)), "days": len(days)}
    return model


def predict_tables(frame, model, protocol):
    out = frame.copy()
    keys = geometry_keys(out, protocol)
    cuts = ["missing" if not np.isfinite(v) else "low" if v <= model["volatility_cut"] else "high" for v in out.vol_240]
    out["geometry_key"] = keys
    out["vol_key"] = [k + "|" + v for k, v in zip(keys, cuts)]
    out[MODELS[0]] = model["global_mean"]
    out[MODELS[1]] = [model["geometry"].get(k, {}).get("prediction", model["global_mean"]) for k in keys]
    out[MODELS[2]] = [model["geometry_vol"].get(k, {}).get("prediction", parent) for k, parent in zip(out.vol_key, out[MODELS[1]])]
    out["GEOMETRY_TABLE_supported"] = [k in model["geometry"] for k in keys]
    out["GEOMETRY_VOL_TABLE_supported"] = [k in model["geometry_vol"] for k in out.vol_key]
    return out


def summarize_prediction(frame, model_name):
    if frame.empty:
        return {"rows": 0, "days": 0}
    weights = day_weights(frame)
    actual, predicted = frame.delta_normalized.to_numpy(), frame[model_name].to_numpy()
    error = predicted - actual
    denom = frame.actual_width / frame.entry_price
    return {"rows": len(frame), "days": int(frame.delivery_date.nunique()),
            "actual_delta_mean": mean(actual, weights), "predicted_delta_mean": mean(predicted, weights),
            "mse": mean(error ** 2, weights), "mae": mean(abs(error), weights), "bias": mean(error, weights),
            "actual_delta_btc_mean": mean(frame.delta_btc, weights),
            "predicted_credit_concession_btc_mean": mean(predicted * denom, weights),
            "nonfallback_date_weight_fraction": 0.0 if model_name == MODELS[0] else mean(frame[model_name + "_supported"], weights),
            "actual_credit": None, "true_ev": None, "true_win_rate": None}


def calendar_bootstrap_daily(daily, confidence, reps=2000, seed=20260921):
    """Calendar seven-day nonoverlapping blocks, shared paired loss differences."""
    dates = pd.to_datetime(daily.index)
    blocks = np.asarray((dates - pd.Timestamp("1970-01-01")).days // 7)
    vals = pd.DataFrame({"block": blocks, "value": daily.to_numpy(float)}).groupby("block").agg(total=("value", "sum"), count=("value", "size"))
    if len(vals) < 2:
        return {"lower": None, "upper": None, "blocks": len(vals), "confidence": confidence}
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(vals), size=(reps, len(vals)))
    draws = vals.total.to_numpy()[indices].sum(axis=1) / vals["count"].to_numpy()[indices].sum(axis=1)
    alpha = (1 - confidence) / 2
    low, high = np.quantile(draws, [alpha, 1 - alpha])
    return {"lower": float(low), "upper": float(high), "blocks": len(vals), "confidence": confidence,
            "replicates": reps, "seed": seed, "development_only": True}


def compare(frame, challenger, baseline, protocol, support):
    f = frame.copy()
    f["mse_difference"] = (f[challenger] - f.delta_normalized) ** 2 - (f[baseline] - f.delta_normalized) ** 2
    daily = f.groupby("delivery_date").mse_difference.mean()
    annual = {str(y): float(g.groupby("delivery_date").mse_difference.mean().mean()) for y, g in f.groupby("evaluation_year")}
    interval = calendar_bootstrap_daily(daily, .9875, protocol["uncertainty"]["replicates"], protocol["uncertainty"]["seed"])
    c, b = summarize_prediction(f, challenger), summarize_prediction(f, baseline)
    without_best = daily.sort_values().iloc[10:]
    gates = {"paired_support": bool(support), "nonfallback_at_least_half": c["nonfallback_date_weight_fraction"] >= .5,
             "mse_ci_upper_negative": interval["upper"] is not None and interval["upper"] < 0,
             "nonworse_three_of_four_years": sum(v <= 0 for v in annual.values()) >= 3 and len(annual) == 4,
             "absolute_bias_nonworse": abs(c["bias"]) <= abs(b["bias"]),
             "improvement_without_ten_best_days": len(without_best) > 0 and float(without_best.mean()) < 0}
    return {"challenger": challenger, "baseline": baseline, "mse_difference": float(daily.mean()),
            "interval": interval, "annual_mse_difference": annual,
            "without_ten_most_favorable_days": float(without_best.mean()) if len(without_best) else None,
            "ten_most_favorable_dates": [{"date": d, "mse_difference": float(v)} for d, v in daily.nsmallest(10).items()],
            "ten_most_adverse_dates": [{"date": d, "mse_difference": float(v)} for d, v in daily.nlargest(10).items()],
            "gates": gates, "development_supported": all(gates.values()), "production_qualified": False}


def fixed_bin_calibration(frame, model_name):
    edges = [0.0, .01, .025, .05, .1, .2, .4, 1.0]
    f = frame.copy()
    f["bin"] = np.searchsorted(edges, f[model_name].to_numpy(), side="right")
    base_weights = pd.Series(day_weights(f), index=f.index)
    result = []
    for label, part in f.groupby("bin"):
        weights = base_weights.loc[part.index].to_numpy()
        result.append({"bin": int(label), "rows": len(part), "days": int(part.delivery_date.nunique()),
                       "date_weight": float(weights.sum()), "prediction": mean(part[model_name], weights),
                       "actual": mean(part.delta_normalized, weights)})
    return {"edges": edges, "weighting": "original side/day row weights retained inside each bin", "bins": result}


def high_saving_half(frame, column):
    """Diagnostic only: date-weighted highest predicted half, fractional ties."""
    x, w = frame[column].to_numpy(), day_weights(frame)
    mask = np.zeros(len(frame))
    remaining = .5 * w.sum()
    for score in np.unique(x)[::-1]:
        at = x == score
        mass = w[at].sum()
        frac = min(1., max(0., remaining / mass))
        mask[at] = frac
        remaining -= mass * frac
        if remaining <= 1e-12:
            break
    return {"coverage": float(np.dot(mask, w) / w.sum()),
            "selected_delta": mean(frame.delta_normalized, w * mask),
            "remaining_delta": mean(frame.delta_normalized, w * (1 - mask)),
            "operational_threshold": None, "scope": "retrospective discrimination, not economic selection"}


def verify_protocol(path):
    path = Path(path)
    protocol = json.loads(path.read_text("utf-8-sig"))
    seal = json.loads((path.parent / "protocol_seal.json").read_text("utf-8-sig"))
    if sha256(path) != seal["protocol_sha256"]:
        raise ValueError("Protocol seal mismatch")
    manifest = path.parent / "source_manifest.json"
    if sha256(manifest) != protocol["source_manifest_sha256"]:
        raise ValueError("Source manifest seal mismatch")
    amendment_path = path.parent / "preresult_amendment_01.json"
    expected = (path.parent / "preresult_amendment_01.sha256").read_text("utf-8").strip()
    if sha256(amendment_path) != expected:
        raise ValueError("Pre-result amendment seal mismatch")
    amendment = json.loads(amendment_path.read_text("utf-8"))
    if amendment["original_protocol_sha256"] != seal["protocol_sha256"] or amendment["new_results_observed"]:
        raise ValueError("Invalid amendment identity")
    protocol["structure"]["fixed_width_fraction_edges"] = amendment["changes"]["fixed_width_fraction_edges"]
    return protocol


def run(ledger, protocol_path, output, research_v11):
    ledger, output, research_v11 = Path(ledger), Path(output), Path(research_v11)
    output.mkdir(parents=True, exist_ok=False)
    try:
        protocol = verify_protocol(protocol_path)
        sources = json.loads((Path(protocol_path).parent / "source_manifest.json").read_text("utf-8"))["files"]
        for path, item in sources.items():
            if sha256(path) != item["sha256"]:
                raise ValueError(f"Frozen source changed: {path}")
        pair_path = ledger / "paired_structure_rows.csv"
        ledger_manifest = json.loads((ledger / "manifest.json").read_text("utf-8-sig"))
        for filename in ("paired_structure_rows.csv", "pairing_gaps.csv"):
            if sha256(ledger / filename) != ledger_manifest["outputs"][filename]["sha256"]:
                raise ValueError("Ledger output hash mismatch")
        frame = prepare_features(pd.read_csv(pair_path))
        if frame.row_id.duplicated().any():
            raise ValueError("Duplicate paired row_id")
        # Original population supplies the pairing coverage denominator, not only survivors.
        original_parts = []
        for year in range(2020, 2026):
            path = research_v11 / f"step30/model_input/model_rows-{year}.csv"
            cols = ["row_id", "as_of_ms", "entry_ms", "expiry_ms", "delivery_date", "side", "target_width", "short_distance_fraction", "actual_width",
                    "entry_price", "short_strike", "long_strike", "short_name", "long_name", "short_creation_ms", "long_creation_ms",
                    "settlement_price", "payout_btc", "loss_normalized"]
            part = pd.read_csv(path, usecols=cols)
            original_parts.append(part[part.target_width == 2000])
        original = pd.concat(original_parts, ignore_index=True)
        if original.row_id.duplicated().any() or not frame.row_id.isin(original.row_id).all():
            raise ValueError("Pair IDs inconsistent with original universe")
        gaps = pd.read_csv(ledger / "pairing_gaps.csv")
        if gaps.row_id.duplicated().any():
            raise ValueError("Duplicate gap identity")
        if len(gaps):
            forbidden = gaps.reason.str.contains("mismatch|invalid|after_entry", regex=True, na=True)
            if forbidden.any():
                raise ValueError("Ledger contains source identity/time integrity gaps; stop affected experiment")
        if set(frame.row_id) & set(gaps.row_id) or set(frame.row_id) | set(gaps.row_id) != set(original.row_id):
            raise ValueError("Pair/gap ledger does not conserve original population")
        delivery_paths = [p for p in sources if Path(p).name == "delivery_prices.json"]
        instrument_paths = [p for p in sources if Path(p).name == "instruments.json"]
        if len(delivery_paths) != 1 or len(instrument_paths) != 1:
            raise ValueError("Ambiguous sealed official metadata")
        deliveries = {x["date"]: x["delivery_price"] for x in json.loads(Path(delivery_paths[0]).read_text("utf-8-sig"))}
        instruments = json.loads(Path(instrument_paths[0]).read_text("utf-8-sig"))
        parity = validate_ledger(frame, original, deliveries, instruments)
        save_json(output / "independent_input_parity.json", parity)
        forecasts, models, windows, coverages, cal_reports = [], {}, {}, {}, {}
        for year in protocol["identity"]["years"]:
            (fit, cal, ev), metadata = split_year(frame, year)
            (_, _, eligible), _ = split_year(original, year)
            windows[str(year)] = metadata
            for side in ("put", "call"):
                a, c, e, source = [p[p.side == side].copy() for p in (fit, cal, ev, eligible)]
                key = f"{year}_{side}"
                coverages[key] = {"original_rows": len(source), "paired_rows": len(e),
                                  "paired_fraction": len(e)/len(source) if len(source) else 0,
                                  "paired_days": int(e.delivery_date.nunique()), "original_days": int(source.delivery_date.nunique())}
                if a.empty or e.empty:
                    raise ValueError(f"Insufficient fold population: {key}")
                model = fit_tables(a, protocol)
                models[key] = model
                cp, ep = predict_tables(c, model, protocol), predict_tables(e, model, protocol)
                cal_reports[key] = {m: summarize_prediction(cp, m) for m in MODELS}
                ep["evaluation_year"] = year
                forecasts.append(ep)
        result = pd.concat(forecasts, ignore_index=True)
        if result.row_id.duplicated().any():
            raise ValueError("Evaluation overlap")
        for name in MODELS:
            result[name + "_credit_concession_btc"] = result[name] * result.actual_width / result.entry_price
        result.to_csv(output / "structure_predictions.csv", index=False, encoding="utf-8")
        summary = {"schema": "astra_structure_evaluation@1.3.0", "protocol_sha256": sha256(protocol_path),
                   "amendment_sha256": sha256(Path(protocol_path).parent / "preresult_amendment_01.json"),
                   "paired_input_sha256": sha256(pair_path), "scope": "already-studied development; payoff differences only",
                   "input_parity": parity,
                   "rows": len(result), "days": int(result.delivery_date.nunique()), "windows": windows,
                   "coverage": coverages, "calibration_period": cal_reports, "sides": {},
                   "new_llm_calls": 0, "runtime_replacement": False, "natural_nr_effect": None,
                   "actual_credit": None, "true_ev": None, "tail_probability_output": None}
        daily_parts = []
        for side, part in result.groupby("side"):
            weights = day_weights(part)
            original_rows = sum(v["original_rows"] for k, v in coverages.items() if k.endswith("_" + side))
            paired_fraction = len(part) / original_rows if original_rows else 0
            support = (part.delivery_date.nunique() >= 200 and paired_fraction >= .5 and
                       all(coverages[f"{y}_{side}"]["paired_days"] >= 30 for y in protocol["identity"]["years"]))
            info = {"pairing_support": bool(support), "paired_fraction": paired_fraction,
                    "models": {m: summarize_prediction(part, m) for m in MODELS},
                    "fixed_bin_calibration": {m: fixed_bin_calibration(part, m) for m in MODELS},
                    "high_predicted_saving_half": {m: high_saving_half(part, m) for m in MODELS},
                    "full_payoff": {"original_mean": mean(part.loss_normalized, weights),
                                    "outward_mean": mean(part.outward_loss_normalized, weights),
                                    "original_es95": es95(part.loss_normalized, weights),
                                    "outward_es95": es95(part.outward_loss_normalized, weights),
                                    "original_over_one_rows": int((part.loss_normalized > 1).sum()),
                                    "outward_over_one_rows": int((part.outward_loss_normalized > 1).sum())},
                    "comparisons": []}
            for challenger, baseline in zip(MODELS[1:], MODELS[:-1]):
                info["comparisons"].append(compare(part, challenger, baseline, protocol, support))
            summary["sides"][side] = info
            metrics = pd.DataFrame({"delivery_date": part.delivery_date, "side": side, "actual_delta": part.delta_normalized})
            for model_name in MODELS:
                metrics[model_name + "_mse"] = (part[model_name] - part.delta_normalized) ** 2
                metrics[model_name + "_prediction"] = part[model_name]
            daily_parts.append(metrics.groupby(["delivery_date", "side"], as_index=False).mean())
        pd.concat(daily_parts).to_csv(output / "daily_metrics.csv", index=False)
        save_json(output / "table_models.json", {"schema": "astra_structure_tables@1.3.0", "research_only": True,
                                                 "protocol_sha256": sha256(protocol_path), "models": models})
        save_json(output / "summary.json", summary)
        lines = ["# 同侧结构差额：开发诊断", "", "无同期信用，不报告真实EV或交易资格。原始完整尾损保留。", "",
                 "| 侧别 | 比较 | 差额MSE变化 | 开发保留 |", "|---|---|---:|---|"]
        for side, info in summary["sides"].items():
            for comp in info["comparisons"]:
                lines.append(f"| {side} | {comp['challenger']} vs {comp['baseline']} | {comp['mse_difference']:.8f} | {comp['development_supported']} |")
        (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary
    except Exception as exc:
        save_json(output / "failure.json", {"error": str(exc), "traceback": traceback.format_exc(), "preserved": True})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", required=True)
    p.add_argument("--protocol", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--research-v11", required=True)
    args = p.parse_args()
    summary = run(args.ledger, args.protocol, args.output, args.research_v11)
    print(json.dumps({"rows": summary["rows"], "days": summary["days"], "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
