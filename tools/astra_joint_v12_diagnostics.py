"""Read-only diagnosis of sealed v1.1 predictions; never fit or select a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

CANDIDATES = {
    "geometry": "gam__geometry__C0.1__A0.1",
    "statistical": "catboost__statistical__D4",
    "joint": "catboost__joint__D3",
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def day_mean(frame, values):
    return float(pd.Series(np.asarray(values, dtype=float), index=frame.index).groupby(frame.delivery_date).mean().mean())


def weighted_es(frame, values, quantile=.95):
    values = np.asarray(values, dtype=float)
    weights = (1 / frame.groupby("delivery_date").delivery_date.transform("size")).to_numpy()
    weights = weights / weights.sum()
    order = np.argsort(values)
    weights, values = weights[order], values[order]
    before = np.cumsum(weights) - weights
    included = np.maximum(0., np.minimum(weights, before + weights - quantile))
    return float(np.dot(included, values) / (1 - quantile))


def summarize_pairs(frame):
    if frame.empty:
        return {"pairs": 0, "delivery_days": 0}
    result = {"pairs": len(frame), "delivery_days": int(frame.delivery_date.nunique())}
    actual_delta = frame.actual_put - frame.actual_call
    actual_center = (frame.actual_put + frame.actual_call) / 2
    actual_tie = np.isclose(actual_delta, 0, atol=1e-12, rtol=0)
    result["both_sides_equal_fraction"] = day_mean(frame, actual_tie)
    for group in CANDIDATES:
        p, c = frame[f"{group}_put"], frame[f"{group}_call"]
        delta, center = p - c, (p + c) / 2
        choice = delta <= 0  # deterministic Put tie convention, disclosed in report
        selected = np.where(choice, frame.actual_put, frame.actual_call)
        error_p, error_c = p - frame.actual_put, c - frame.actual_call
        ep, ec = day_mean(frame, error_p), day_mean(frame, error_c)
        cov = day_mean(frame, (error_p - ep) * (error_c - ec))
        variance = day_mean(frame, (error_p - ep)**2) * day_mean(frame, (error_c - ec)**2)
        result[group] = {
            "selected_actual_loss": day_mean(frame, selected),
            "selected_positive_payout_rate": day_mean(frame, selected > 0),
            "selected_es95": weighted_es(frame, selected),
            "put_choice_fraction": day_mean(frame, choice),
            "pair_center_mse": day_mean(frame, (center - actual_center)**2),
            "pair_delta_mse": day_mean(frame, (delta - actual_delta)**2),
            "paired_row_mse": day_mean(frame, ((error_p**2) + (error_c**2)) / 2),
            "regret_vs_hindsight_min": day_mean(frame, selected - np.minimum(frame.actual_put, frame.actual_call)),
            "side_error_correlation": float(cov / np.sqrt(variance)) if variance > 0 else None,
            "correct_on_unequal_actual": day_mean(frame.loc[~actual_tie], (choice == (actual_delta < 0))[~actual_tie]) if (~actual_tie).any() else None,
            "predicted_ties": int(np.isclose(delta, 0, atol=1e-12, rtol=0).sum()),
        }
    result["fixed_put"] = {"selected_actual_loss": day_mean(frame, frame.actual_put), "es95": weighted_es(frame, frame.actual_put)}
    result["fixed_call"] = {"selected_actual_loss": day_mean(frame, frame.actual_call), "es95": weighted_es(frame, frame.actual_call)}
    result["equal_mix_loss"] = day_mean(frame, actual_center)
    for group in ("statistical", "joint"):
        base_put = frame.geometry_put <= frame.geometry_call
        new_put = frame[f"{group}_put"] <= frame[f"{group}_call"]
        difference = np.where(new_put, frame.actual_put, frame.actual_call) - np.where(base_put, frame.actual_put, frame.actual_call)
        flip = base_put != new_put
        result[f"{group}_vs_geometry"] = {
            "flips": int(flip.sum()), "flip_fraction": day_mean(frame, flip),
            "harmful_flips": int((difference > 1e-12).sum()),
            "helpful_flips": int((difference < -1e-12).sum()),
            "outcome_neutral_flips": int((flip & (abs(difference) <= 1e-12)).sum()),
            "mean_added_loss": day_mean(frame, difference),
            "harm_contribution": day_mean(frame, np.maximum(difference, 0)),
            "benefit_contribution": day_mean(frame, np.minimum(difference, 0)),
        }
    return result


def load_predictions(research):
    folder = research / "step30/models_annual_calibrated_20260921/rolling_candidate_predictions"
    frames, identities = {}, {}
    for group, candidate in CANDIDATES.items():
        path = folder / f"{candidate}.csv"
        df = pd.read_csv(path)
        if df.row_id.duplicated().any():
            raise ValueError("duplicate prediction row_id")
        if not (df.target_width == 2000).all():
            raise ValueError("unexpected primary width")
        frames[group] = df.set_index("row_id").sort_index()
        identities[group] = {"candidate": candidate, "sha256": sha256(path), "rows": len(df)}
    base = frames["geometry"]
    for group, frame in frames.items():
        pd.testing.assert_index_equal(base.index, frame.index)
        for column in ("observation_id", "side", "delivery_date", "actual_width", "actual_loss_normalized", "protection_breached"):
            pd.testing.assert_series_equal(base[column], frame[column], check_names=False)
    return frames, identities


def build_pairs(frames):
    base = frames["geometry"]
    meta = ["observation_id", "delivery_date", "as_of_ms", "entry_ms", "expiry_ms", "actual_width", "actual_loss_normalized"]
    put = base[base.side == "put_credit"][meta].reset_index()
    call = base[base.side == "call_credit"][meta].reset_index()
    joined = put.merge(call, on="observation_id", suffixes=("_put", "_call"), validate="one_to_one")
    if not (joined.delivery_date_put == joined.delivery_date_call).all() or not (joined.expiry_ms_put == joined.expiry_ms_call).all():
        raise ValueError("paired expiry mismatch")
    if not (joined.as_of_ms_put == joined.as_of_ms_call).all() or not (joined.entry_ms_put == joined.entry_ms_call).all():
        raise ValueError("paired observation/entry time mismatch")
    same_width = np.isclose(joined.actual_width_put, joined.actual_width_call, rtol=0, atol=1e-9)
    counts = {"all_pairs": len(joined), "same_width_pairs": int(same_width.sum()), "unequal_width_pairs_retained_in_source": int((~same_width).sum()), "unpaired_rows": len(base) - 2 * len(joined)}
    result = joined.loc[same_width].copy().reset_index(drop=True)
    result["delivery_date"] = result.delivery_date_put
    result["actual_put"], result["actual_call"] = result.actual_loss_normalized_put, result.actual_loss_normalized_call
    result["dte_hours"] = (result.expiry_ms_put - result.as_of_ms_put) / 3600000
    for group, frame in frames.items():
        for side in ("put", "call"):
            result[f"{group}_{side}"] = result[f"row_id_{side}"].map(frame.expected_loss_normalized)
            result[f"{group}_prob_{side}"] = result[f"row_id_{side}"].map(frame.probability_positive)
    return result, counts


def run(research, output):
    output.mkdir(parents=True, exist_ok=False)
    frames, identities = load_predictions(research)
    pairs, counts = build_pairs(frames)
    needed = {"row_id", "vol_240", "ret_240", "ret_1440", "net_flow_240", "short_distance_fraction", "entry_price"}
    parts = []
    source_hashes = {}
    for path in sorted((research / "step30/model_input").glob("*.csv")):
        # Only four already-researched development years participate in diagnosis.
        if path.stem[-4:] not in ("2022", "2023", "2024", "2025"):
            continue
        source_hashes[path.name] = sha256(path)
        for chunk in pd.read_csv(path, usecols=lambda column: column in needed, chunksize=25000):
            parts.append(chunk[chunk.row_id.isin(pairs.row_id_put)])
    features = pd.concat(parts).set_index("row_id")
    if features.index.duplicated().any():
        raise ValueError("duplicate model input identity")
    for column in needed - {"row_id"}:
        pairs[column] = pairs.row_id_put.map(features[column])
    if pairs.vol_240.isna().any():
        raise ValueError("missing source features")
    pairs["year"] = pairs.delivery_date.str[:4]
    pairs["month"] = pairs.delivery_date.str[:7]
    pairs["dte_band"] = pd.cut(pairs.dte_hours, [8, 12, 16, 20, 24], include_lowest=False).astype(str)
    pairs["vol_band"] = pd.cut(pairs.vol_240, [-np.inf, .005, .01, .02, np.inf]).astype(str)
    pairs["return_sign"] = np.where(pairs.ret_240 >= 0, "nonnegative_4h", "negative_4h")
    pairs["statistical_delta"] = pairs.statistical_put - pairs.statistical_call
    pairs["geometry_delta"] = pairs.geometry_put - pairs.geometry_call
    pairs["actual_delta"] = pairs.actual_put - pairs.actual_call
    pairs["predicted_gap_band"] = pd.cut(abs(pairs.statistical_delta), [-1e-15, .01, .05, .1, np.inf]).astype(str)
    pairs["statistical_added_loss"] = np.where(pairs.statistical_delta <= 0, pairs.actual_put, pairs.actual_call) - np.where(pairs.geometry_delta <= 0, pairs.actual_put, pairs.actual_call)
    daily = pairs.groupby("delivery_date").statistical_added_loss.mean()
    total_days = len(daily)
    daily_table = daily.rename("added_loss").to_frame()
    daily_table["contribution_to_total_mean"] = daily / total_days
    daily_table.to_csv(output / "all_date_contributions.csv")
    report = {
        "schema": "astra_joint_v12_diagnosis@1.0.0",
        "scope": "2022-2025 already-studied common-clock development; not natural NR, independent test, or economic returns",
        "method": "delivery-day-equal within each reported subset; deterministic Put on exact predicted tie; no retraining or threshold search; bins descriptive only",
        "decomposition": "paired row MSE = center MSE + delta MSE / 4",
        "prediction_sources": identities, "input_sha256": source_hashes,
        "coverage": counts, "overall": summarize_pairs(pairs),
        "strata": {key: {str(value): summarize_pairs(group) for value, group in pairs.groupby(key, sort=True)} for key in ("year", "month", "dte_band", "vol_band", "return_sign", "predicted_gap_band")},
        "date_concentration": {
            "ten_largest_harm_dates": daily_table.nlargest(10, "added_loss").reset_index().to_dict("records"),
            "ten_largest_benefit_dates": daily_table.nsmallest(10, "added_loss").reset_index().to_dict("records"),
            "mean_all_dates": float(daily.mean()),
            "leave_ten_largest_harm_dates_out_diagnostic_only": float(daily.drop(daily.nlargest(10).index).mean()),
            "leave_ten_largest_benefit_dates_out_diagnostic_only": float(daily.drop(daily.nsmallest(10).index).mean()),
            "positive_contribution_dates": int((daily > 0).sum()), "negative_contribution_dates": int((daily < 0).sum()),
        },
        "all_row_metrics": {},
        "net_win_rate": None, "mean_net_result": None,
        "economics_reason": "no contemporaneous executable historical credit",
        "natural_nr_effect": "not_estimated; common clock is training research only",
    }
    for group, frame in frames.items():
        tail = pd.to_numeric(frame.breach_probability, errors="coerce")
        report["all_row_metrics"][group] = {
            "rows": len(frame), "delivery_days": int(frame.delivery_date.nunique()),
            "mse": day_mean(frame, (frame.expected_loss_normalized - frame.actual_loss_normalized)**2),
            "occurrence_brier": day_mean(frame, (frame.probability_positive - (frame.actual_loss_normalized > 0))**2),
            "tail_order_violations": int((tail > frame.probability_positive + 1e-12).sum()),
            "loss_above_one_rows": int((frame.actual_loss_normalized > 1).sum()),
            "maximum_realized_loss": float(frame.actual_loss_normalized.max()),
            "calibration": [
                {"bin": str(interval), "rows": len(part), "days": int(part.delivery_date.nunique()),
                 "predicted": day_mean(part, part.probability_positive), "actual": day_mean(part, part.actual_loss_normalized > 0)}
                for interval, part in frame.groupby(pd.cut(frame.probability_positive, np.linspace(0, 1, 11), include_lowest=True), observed=True)
            ],
        }
    pairs.to_csv(output / "same_width_pairs.csv", index=False)
    (output / "diagnosis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.research, args.output)
    print(json.dumps({"coverage": result["coverage"], "overall": result["overall"]}, ensure_ascii=False, indent=2))
