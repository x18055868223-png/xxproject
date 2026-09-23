"""Frozen v1.6 accounting appendices; research only, no model or runtime writes."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import astra_underwriting_v16 as base


EXIT_SCENARIOS = {
    "IV40": (0.4, 0.025),
    "IV60": (0.6, 0.025),
    "IV80": (0.8, 0.025),
    "IV60_COST5": (0.6, 0.05),
}
STRUCTURE_SCENARIOS = {**EXIT_SCENARIOS, "RV4H_X125": (None, 0.025)}

EXIT_METHODS = (
    "HOLD",
    "EXIT_ALL_TOUCH",
    "WINDOWS",
    "DYNAMIC",
    "MIX2",
    "HMM2",
    "HMM_RESET",
)

STRUCTURE_METHODS = ("HIST_SIDE_MEAN", "GEOMETRY_TABLE", "COARSE_GEOM_CAL3M")
SCHEDULES = ("UTC08", "CLOCK30_BUDGET")


def code_hashes():
    return {
        str(Path(__file__)): base.digest(Path(__file__)),
        str(Path(base.__file__)): base.digest(Path(base.__file__)),
    }


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return value


def save(path: Path, value):
    path.write_text(json.dumps(safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def save_new(path: Path, value):
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    save(path, value)


def write_csv_new(frame: pd.DataFrame, path: Path):
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    frame.to_csv(path, index=False)


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def weighted_mean(values, weights):
    x = np.asarray(values, float)
    w = np.asarray(weights, float)
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    return float(np.sum(x[ok] * w[ok]) / np.sum(w[ok])) if ok.any() else np.nan


def weighted_sum_per_day(values, weights, days):
    x = np.asarray(values, float)
    w = np.asarray(weights, float)
    ok = np.isfinite(x) & np.isfinite(w) & (w > 0)
    n_days = len(pd.unique(days))
    return float(np.sum(x[ok] * w[ok]) / n_days) if n_days else np.nan


def daily_portfolio(frame: pd.DataFrame, values) -> pd.Series:
    tmp = pd.DataFrame(
        {
            "delivery_date": frame["delivery_date"].to_numpy(),
            "weighted_value": np.asarray(values, float) * frame["original_weight"].to_numpy(float),
            "known": np.isfinite(values),
        }
    )
    summed = tmp.groupby("delivery_date")["weighted_value"].sum(min_count=1)
    complete = tmp.groupby("delivery_date")["known"].all()
    return summed.where(complete)


def stable_row_hash(frame: pd.DataFrame, columns) -> pd.Series:
    cols = [c for c in columns if c in frame.columns]

    def one(row):
        payload = {c: normalize_hash_value(row[c]) for c in cols}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    return frame[cols].apply(one, axis=1)


def normalize_hash_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return str(value)


def apply_net_loss(payment_loss, initial_credit, entry_cost):
    payment = np.asarray(payment_loss, float)
    credit = np.asarray(initial_credit, float)
    return payment - credit + float(entry_cost)


def pair_metrics(frame: pd.DataFrame, values, side: str, prefix: str):
    side_frame = frame.loc[frame.side.eq(side)].copy()
    x = np.asarray(values, float)[frame.side.eq(side).to_numpy()]
    w = side_frame.original_weight.to_numpy(float)
    daily = daily_portfolio(side_frame, x)
    return {
        f"{prefix}_mean_known_weighted": weighted_mean(x, w),
        f"{prefix}_mean_per_original_day": weighted_sum_per_day(x, w, side_frame.delivery_date),
        f"{prefix}_es95_row_weighted": base.weighted_es(x, w),
        f"{prefix}_es95_daily_portfolio": base.weighted_es(daily.to_numpy(), np.ones(len(daily))),
        f"{prefix}_complete_daily_portfolios": int(daily.notna().sum()),
        f"{prefix}_incomplete_daily_portfolios": int(daily.isna().sum()),
        f"{prefix}_max": float(np.nanmax(x)) if np.isfinite(x).any() else None,
    }


def bs_credit_norm(frame: pd.DataFrame, iv: float, short_col="short_strike", long_col="long_strike", hours=None):
    if hours is None:
        hours = (frame.expiry_ms.to_numpy(float) - frame.entry_ms.to_numpy(float)) / 3600000.0
    denom = frame.actual_width.to_numpy(float) / frame.entry_price.to_numpy(float)
    return (
        base.bs_spread_btc(
            frame.side.to_numpy(),
            frame.entry_price.to_numpy(float),
            frame[short_col].to_numpy(float),
            frame[long_col].to_numpy(float),
            hours,
            iv,
        )
        / denom
    )


def validate_protocol(research: Path):
    protocol_path = research / "protocol_v16.json"
    seal_path = research / "protocol_seal.json"
    protocol = read(protocol_path)
    seal = read(seal_path)
    actual = base.digest(protocol_path)
    if actual != seal["protocol_sha256"]:
        raise ValueError(f"protocol changed: {actual} != {seal['protocol_sha256']}")
    return protocol, seal, actual


def load_exit_inputs(root: Path):
    run = root / ".artifacts/astra-state-audit-v15-20260922/run_02"
    population_path = run / "exit_population.csv"
    population = pd.read_csv(population_path, low_memory=False)
    if population.row_id.duplicated().any():
        raise ValueError("duplicate exit population row_id")
    scenarios = {}
    for name in EXIT_SCENARIOS:
        path = run / f"exit_scenario_{name}.csv"
        frame = pd.read_csv(path, low_memory=False)
        if frame.row_id.duplicated().any():
            raise ValueError(f"duplicate exit scenario row_id: {name}")
        scenarios[name] = (path, frame)
    return population_path, population, scenarios


def recompute_exit_accounting(root: Path, out: Path):
    population_path, population, scenario_frames = load_exit_inputs(root)
    pop_cols = [
        "row_id",
        "side",
        "entry_ms",
        "expiry_ms",
        "delivery_date",
        "actual_width",
        "short_strike",
        "long_strike",
        "entry_price",
        "loss_normalized",
    ]
    pop = population[pop_cols].copy()
    summaries = []
    invariance = []
    source_files = {str(population_path): {"sha256": base.digest(population_path)}}

    for scenario, (path, ledger) in scenario_frames.items():
        iv, entry_cost = EXIT_SCENARIOS[scenario]
        source_files[str(path)] = {"sha256": base.digest(path)}
        f = ledger.merge(pop, on="row_id", how="left", suffixes=("", "_pop"), validate="one_to_one")
        if f.entry_ms.isna().any():
            raise ValueError(f"exit scenario missing population rows: {scenario}")
        for key in ("side", "delivery_date"):
            if not f[key].eq(f[f"{key}_pop"]).all():
                raise ValueError(f"exit strict join mismatch {scenario}/{key}")
        if not np.allclose(f.target.to_numpy(float), f.loss_normalized.to_numpy(float), rtol=0, atol=1e-12):
            raise ValueError(f"exit strict join mismatch target: {scenario}")

        f["initial_credit_norm"] = bs_credit_norm(f, iv)
        f["entry_cost_norm"] = entry_cost
        hash_cols = [
            "row_id",
            "known_path",
            "prediction_available",
            "target",
            "assumed_threshold",
            *[f"{m}_{suffix}" for m in EXIT_METHODS for suffix in ("action", "gain", "loss")],
        ]
        f["source_row_hash"] = stable_row_hash(f, hash_cols)

        out_cols = [
            "row_id",
            "delivery_date",
            "side",
            "original_weight",
            "known_path",
            "prediction_available",
            "target",
            "assumed_threshold",
            "initial_credit_norm",
            "entry_cost_norm",
            "source_row_hash",
        ]
        for method in EXIT_METHODS:
            pay_col = f"{method}_loss"
            net_col = f"{method}_net_loss"
            f[net_col] = apply_net_loss(f[pay_col].to_numpy(float), f.initial_credit_norm.to_numpy(float), entry_cost)
            out_cols.extend([f"{method}_action", f"{method}_gain", pay_col, net_col])

        write_csv_new(f[out_cols], out / f"exit_netloss_{scenario}.csv")

        for side in ("put", "call"):
            side_mask = f.side.eq(side).to_numpy()
            side_rows = f.loc[side_mask].copy()
            dates = side_rows.delivery_date.nunique()
            for method in EXIT_METHODS:
                payment = f[f"{method}_loss"].to_numpy(float)
                net = f[f"{method}_net_loss"].to_numpy(float)
                action = f[f"{method}_action"].to_numpy(bool)
                row = {
                    "appendix": "exit_net_loss_erratum",
                    "scenario": scenario,
                    "side": side,
                    "method": method,
                    "rows": int(side_mask.sum()),
                    "known_rows": int(np.isfinite(payment[side_mask]).sum()),
                    "delivery_days": int(dates),
                    "action_original_weight_per_day": float(
                        np.sum(side_rows.original_weight.to_numpy(float) * action[side_mask]) / dates
                    ),
                    "unknown_original_weight_per_day": float(
                        np.sum(side_rows.original_weight.to_numpy(float) * ~np.isfinite(payment[side_mask])) / dates
                    ),
                    "entry_credit_mean_known_weighted": weighted_mean(
                        f.initial_credit_norm.to_numpy(float)[side_mask], side_rows.original_weight
                    ),
                    "entry_cost_norm": entry_cost,
                    "actual_market_ev": None,
                }
                row.update(pair_metrics(f, payment, side, "old_payment_loss"))
                row.update(pair_metrics(f, net, side, "corrected_net_loss"))
                summaries.append(row)

            for left, right in itertools.combinations(EXIT_METHODS, 2):
                p_delta = f[f"{left}_loss"].to_numpy(float) - f[f"{right}_loss"].to_numpy(float)
                n_delta = f[f"{left}_net_loss"].to_numpy(float) - f[f"{right}_net_loss"].to_numpy(float)
                w = f.original_weight.to_numpy(float)
                invariance.append(
                    {
                        "scenario": scenario,
                        "side": side,
                        "left": left,
                        "right": right,
                        "payment_mean_delta": weighted_mean(p_delta[side_mask], w[side_mask]),
                        "net_mean_delta": weighted_mean(n_delta[side_mask], w[side_mask]),
                        "abs_error": abs(
                            weighted_mean(p_delta[side_mask], w[side_mask])
                            - weighted_mean(n_delta[side_mask], w[side_mask])
                        ),
                    }
                )

    summary = pd.DataFrame(summaries)
    inv = pd.DataFrame(invariance)
    write_csv_new(summary, out / "exit_accounting_summary.csv")
    write_csv_new(inv, out / "exit_mean_delta_invariance.csv")
    return {
        "summary_rows": len(summary),
        "max_mean_delta_invariance_error": float(inv.abs_error.max()),
        "source_files": source_files,
    }


def existing_exit_accounting_info(root: Path, out: Path):
    population_path, _, scenario_frames = load_exit_inputs(root)
    required = [
        out / "exit_accounting_summary.csv",
        out / "exit_mean_delta_invariance.csv",
        *[out / f"exit_netloss_{name}.csv" for name in EXIT_SCENARIOS],
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("cannot resume exit appendix; missing " + ", ".join(missing))
    summary = pd.read_csv(out / "exit_accounting_summary.csv")
    inv = pd.read_csv(out / "exit_mean_delta_invariance.csv")
    source_files = {str(population_path): {"sha256": base.digest(population_path)}}
    for path, _frame in scenario_frames.values():
        source_files[str(path)] = {"sha256": base.digest(path)}
    return {
        "summary_rows": int(len(summary)),
        "max_mean_delta_invariance_error": float(inv.abs_error.max()),
        "source_files": source_files,
        "output_files": {str(path): {"sha256": base.digest(path)} for path in required},
        "resumed_from_existing_failure_directory": True,
    }


def load_structure_inputs(root: Path):
    pred_path = root / ".artifacts/astra-entry-exit-v14-20260922/structure_02/predictions.csv"
    pair_path = root / ".artifacts/astra-entry-quality-v13-20260921/structure_ledger_01/paired_structure_rows.csv"
    pred = pd.read_csv(pred_path, low_memory=False)
    pairs = pd.read_csv(pair_path, low_memory=False)
    if pred.row_id.duplicated().any():
        raise ValueError("duplicate structure prediction row_id")
    if pairs.row_id.duplicated().any():
        raise ValueError("duplicate structure pair row_id")
    return pred_path, pred, pair_path, pairs


def load_structure_rv_rows(research: Path):
    path = research / "run_01/base_rows.csv"
    frame = pd.read_csv(path, usecols=["row_id", "rv4_annualized"], low_memory=False)
    if frame.row_id.duplicated().any():
        raise ValueError("duplicate R16 base row_id for RV join")
    return path, frame


def output_hashes(out: Path):
    result = {}
    for path in sorted(out.iterdir(), key=lambda p: p.name):
        if path.is_file() and path.name != "completion_receipt.json":
            result[str(path)] = {"sha256": base.digest(path), "bytes": path.stat().st_size}
    return result


def write_completion_receipt(
    out: Path,
    protocol_sha: str,
    exit_info: dict,
    structure_info: dict,
    resumed: bool,
    reuse_exit_from: Path | None,
):
    receipt = {
        "schema": "astra_accounting_v16_completion_receipt@1.0.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "output": str(out),
        "protocol_sha256": protocol_sha,
        "code_sha256": code_hashes(),
        "fresh_full_cli_run": bool(not resumed and reuse_exit_from is None),
        "exit_price_scenarios": list(EXIT_SCENARIOS),
        "structure_price_scenarios": list(STRUCTURE_SCENARIOS),
        "exit_summary_rows": exit_info["summary_rows"],
        "exit_max_mean_delta_invariance_error": exit_info["max_mean_delta_invariance_error"],
        "structure_summary_rows": structure_info["summary_rows"],
        "structure_mother_rows": structure_info["mother_rows"],
        "structure_paired_rows": structure_info["paired_rows"],
        "structure_missing_pair_rows": structure_info["missing_pair_rows"],
        "structure_max_cashflow_identity_error": structure_info["max_cashflow_identity_error"],
        "rv4h_identity": "structure only, from run_01/base_rows.csv rv4_annualized; exit remains the four completed v15 scenarios",
        "actual_market_ev": None,
        "natural_nr_effect": None,
        "runtime_model_changed": False,
        "production_changed": False,
        "output_files": output_hashes(out),
    }
    save_new(out / "completion_receipt.json", receipt)
    return receipt


def structure_policy_arrays(predicted_saving, actual_saving, credit_concession, original_net_loss, outward_net_loss, pair_available):
    pred = np.asarray(predicted_saving, float)
    actual = np.asarray(actual_saving, float)
    q = np.asarray(credit_concession, float)
    original = np.asarray(original_net_loss, float)
    outward = np.asarray(outward_net_loss, float)
    pair = np.asarray(pair_available, bool)
    action = pair & np.isfinite(pred) & np.isfinite(actual) & np.isfinite(q) & (pred > q)
    gain = np.where(action, actual - q, 0.0)
    net_loss = np.where(action, outward, original)
    return action, gain, net_loss


def validate_structure_identity(frame: pd.DataFrame):
    for key in ("side", "delivery_date"):
        left = frame[key].astype(str)
        for suffix in ("_pair", "_pred"):
            col = key + suffix
            if col in frame and not left[frame[col].notna()].eq(frame.loc[frame[col].notna(), col].astype(str)).all():
                raise ValueError(f"structure strict join mismatch: {key}{suffix}")
    for key in (
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "actual_width",
        "entry_price",
        "short_strike",
        "long_strike",
        "loss_normalized",
    ):
        for suffix in ("_pair", "_pred"):
            col = key + suffix
            if col in frame:
                ix = frame[col].notna()
                if not np.allclose(frame.loc[ix, key], frame.loc[ix, col], rtol=0, atol=1e-10):
                    raise ValueError(f"structure strict join mismatch: {key}{suffix}")


def structure_schedule(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    f = base.schedule(frame, name).copy()
    return f


def structure_credits(frame: pd.DataFrame, scenario: str):
    iv, entry_cost = STRUCTURE_SCENARIOS[scenario]
    sigma = (
        np.full(len(frame), float(iv), dtype=float)
        if iv is not None
        else 1.25 * frame.rv4_annualized.to_numpy(float)
    )
    valid = np.isfinite(sigma) & (sigma > 0)
    orig = np.full(len(frame), np.nan)
    outward = np.full(len(frame), np.nan)
    if valid.any():
        orig[valid] = bs_credit_norm(frame.loc[valid], sigma[valid])
    outward_valid = valid & frame.pair_available.to_numpy(bool)
    if outward_valid.any():
        outward[outward_valid] = bs_credit_norm(
            frame.loc[outward_valid],
            sigma[outward_valid],
            short_col="outward_short_strike",
            long_col="outward_long_strike",
        )
    return orig, outward, entry_cost, sigma


def recompute_structure_accounting(root: Path, out: Path):
    original, original_sources = base.load_original(root)
    pred_path, pred, pair_path, pairs = load_structure_inputs(root)
    rv_path, rv_rows = load_structure_rv_rows(out.parent)

    pair_cols = [
        "row_id",
        "side",
        "delivery_date",
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "actual_width",
        "entry_price",
        "short_strike",
        "long_strike",
        "outward_short_strike",
        "outward_long_strike",
        "loss_normalized",
        "outward_loss_normalized",
        "delta_normalized",
        "payout_btc",
        "outward_payout_btc",
    ]
    pred_cols = [
        "row_id",
        "side",
        "delivery_date",
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "actual_width",
        "entry_price",
        "loss_normalized",
        "outward_loss_normalized",
        "delta_normalized",
        *STRUCTURE_METHODS,
    ]
    base_frame = original[
        [
            "row_id",
            "side",
            "delivery_date",
            "as_of_ms",
            "entry_ms",
            "expiry_ms",
            "actual_width",
            "entry_price",
            "short_strike",
            "long_strike",
            "loss_normalized",
        ]
    ].copy()
    frame = base_frame.merge(pairs[pair_cols], on="row_id", how="left", suffixes=("", "_pair"), validate="one_to_one")
    frame = frame.merge(pred[pred_cols], on="row_id", how="left", suffixes=("", "_pred"), validate="one_to_one")
    frame = frame.merge(rv_rows, on="row_id", how="left", validate="one_to_one")
    if frame.rv4_annualized.isna().any():
        raise ValueError("missing rv4_annualized for structure row_id")
    validate_structure_identity(frame)
    frame["pair_available"] = frame.outward_short_strike.notna()
    frame["prediction_available"] = frame[list(STRUCTURE_METHODS)].notna().any(axis=1)
    if len(frame) != 93242:
        raise ValueError(f"unexpected mother denominator rows: {len(frame)}")

    source_files = {
        str(pred_path): {"sha256": base.digest(pred_path)},
        str(pair_path): {"sha256": base.digest(pair_path)},
        str(rv_path): {"sha256": base.digest(rv_path)},
        **{path: {"sha256": sha} for path, sha in original_sources.items()},
    }
    summaries = []
    identity_rows = []

    for schedule_name in SCHEDULES:
        sched = structure_schedule(frame, schedule_name)
        for scenario in STRUCTURE_SCENARIOS:
            f = sched.copy()
            orig_credit, outward_credit, entry_cost, sigma = structure_credits(f, scenario)
            f["orig_credit_norm"] = orig_credit
            f["outward_credit_norm"] = outward_credit
            f["assumed_iv"] = sigma
            f["credit_concession_norm"] = f.orig_credit_norm - f.outward_credit_norm
            f["entry_cost_norm"] = entry_cost
            f["original_net_loss"] = f.loss_normalized.to_numpy(float) - f.orig_credit_norm.to_numpy(float) + entry_cost
            f["outward_net_loss"] = (
                f.outward_loss_normalized.to_numpy(float) - f.outward_credit_norm.to_numpy(float) + entry_cost
            )
            f["actual_saving_norm"] = f.delta_normalized
            f["cashflow_identity_error"] = (
                f.original_net_loss.to_numpy(float)
                - f.outward_net_loss.to_numpy(float)
                - (f.actual_saving_norm.to_numpy(float) - f.credit_concession_norm.to_numpy(float))
            )

            ledger_cols = [
                "row_id",
                "delivery_date",
                "side",
                "original_weight",
                "pair_available",
                "prediction_available",
                "loss_normalized",
                "outward_loss_normalized",
                "actual_saving_norm",
                "orig_credit_norm",
                "outward_credit_norm",
                "assumed_iv",
                "credit_concession_norm",
                "entry_cost_norm",
                "original_net_loss",
                "outward_net_loss",
                "cashflow_identity_error",
            ]
            for method in STRUCTURE_METHODS:
                action, gain, net_loss = structure_policy_arrays(
                    f[method].to_numpy(float),
                    f.actual_saving_norm.to_numpy(float),
                    f.credit_concession_norm.to_numpy(float),
                    f.original_net_loss.to_numpy(float),
                    f.outward_net_loss.to_numpy(float),
                    f.pair_available.to_numpy(bool),
                )
                f[f"{method}_action"] = action
                f[f"{method}_gain"] = gain
                f[f"{method}_net_loss"] = net_loss
                ledger_cols.extend([method, f"{method}_action", f"{method}_gain", f"{method}_net_loss"])

            f["source_row_hash"] = stable_row_hash(
                f,
                [
                    "row_id",
                    "pair_available",
                    "prediction_available",
                    "loss_normalized",
                    "outward_loss_normalized",
                    "actual_saving_norm",
                    "orig_credit_norm",
                    "outward_credit_norm",
                    "credit_concession_norm",
                    *STRUCTURE_METHODS,
                ],
            )
            write_csv_new(f[[*ledger_cols, "source_row_hash"]], out / f"structure_{schedule_name}_{scenario}.csv")

            pair_error = np.abs(f.loc[f.pair_available, "cashflow_identity_error"].to_numpy(float))
            identity_rows.append(
                {
                    "schedule": schedule_name,
                    "scenario": scenario,
                    "paired_rows": int(f.pair_available.sum()),
                    "max_abs_cashflow_identity_error": float(np.nanmax(pair_error)) if len(pair_error) else None,
                }
            )

            for side in ("put", "call"):
                side_rows = f.loc[f.side.eq(side)].copy()
                dates = side_rows.delivery_date.nunique()
                pair_weight = np.sum(side_rows.original_weight.to_numpy(float) * side_rows.pair_available.to_numpy(bool))
                side_weight = np.sum(side_rows.original_weight.to_numpy(float))
                for method in STRUCTURE_METHODS:
                    action = side_rows[f"{method}_action"].to_numpy(bool)
                    gain = side_rows[f"{method}_gain"].to_numpy(float)
                    method_net_full = f[f"{method}_net_loss"].to_numpy(float)
                    row = {
                        "appendix": "structure_credit_concession",
                        "schedule": schedule_name,
                        "scenario": scenario,
                        "side": side,
                        "method": method,
                        "mother_original_rows": int(len(side_rows)),
                        "mother_delivery_days": int(dates),
                        "paired_rows": int(side_rows.pair_available.sum()),
                        "pair_available_original_weight_fraction": float(pair_weight / side_weight),
                        "missing_pair_original_weight_fraction": float(1.0 - pair_weight / side_weight),
                        "action_original_weight_per_day": float(
                            np.sum(side_rows.original_weight.to_numpy(float) * action) / dates
                        ),
                        "gain_original_denominator": weighted_sum_per_day(gain, side_rows.original_weight, side_rows.delivery_date),
                        "mean_gain_paired_weighted": weighted_mean(
                            gain[side_rows.pair_available.to_numpy(bool)],
                            side_rows.loc[side_rows.pair_available, "original_weight"],
                        ),
                        "actual_market_ev": None,
                    }
                    row.update(pair_metrics(f, f.original_net_loss.to_numpy(float), side, "original_net_loss"))
                    row.update(pair_metrics(f, method_net_full, side, "policy_net_loss"))
                    outward_values = f.outward_net_loss.to_numpy(float).copy()
                    outward_values[~f.pair_available.to_numpy(bool)] = np.nan
                    row.update(pair_metrics(f, outward_values, side, "outward_net_loss_paired_only"))
                    summaries.append(row)

    write_csv_new(pd.DataFrame(summaries), out / "structure_concession_summary.csv")
    write_csv_new(pd.DataFrame(identity_rows), out / "structure_credit_concession_identity.csv")
    return {
        "summary_rows": len(summaries),
        "mother_rows": int(len(frame)),
        "paired_rows": int(frame.pair_available.sum()),
        "missing_pair_rows": int((~frame.pair_available).sum()),
        "max_cashflow_identity_error": float(pd.DataFrame(identity_rows).max_abs_cashflow_identity_error.max()),
        "source_files": source_files,
    }


def smoke_summary(root: Path):
    population_path, population, scenario_frames = load_exit_inputs(root)
    _scenario_path, exit_ledger = scenario_frames["IV40"]
    sample_ids = exit_ledger.groupby("side", sort=False).head(2).row_id
    pop_cols = [
        "row_id",
        "side",
        "entry_ms",
        "expiry_ms",
        "delivery_date",
        "actual_width",
        "short_strike",
        "long_strike",
        "entry_price",
        "loss_normalized",
    ]
    f = exit_ledger[exit_ledger.row_id.isin(sample_ids)].merge(
        population[pop_cols], on="row_id", suffixes=("", "_pop"), validate="one_to_one"
    )
    f["initial_credit_norm"] = bs_credit_norm(f, EXIT_SCENARIOS["IV40"][0])
    f["HOLD_net_loss"] = apply_net_loss(f.HOLD_loss, f.initial_credit_norm, EXIT_SCENARIOS["IV40"][1])
    exit_rows = [
        {
            "side": side,
            **pair_metrics(f, f.HOLD_net_loss.to_numpy(float), side, "hold_net_loss"),
        }
        for side in ("put", "call")
    ]

    original, _sources = base.load_original(root)
    pred_path, pred, pair_path, pairs = load_structure_inputs(root)
    rv_path, rv_rows = load_structure_rv_rows(root / ".artifacts/astra-underwriting-v16-20260922")
    paired_ids = set(pred.row_id) & set(pairs.row_id)
    sample = pd.concat(
        [
            original[original.side.eq(side) & original.row_id.isin(paired_ids)].head(2)
            for side in ("put", "call")
        ],
        ignore_index=True,
    )
    pair_cols = [
        "row_id",
        "outward_short_strike",
        "outward_long_strike",
        "outward_loss_normalized",
        "delta_normalized",
    ]
    pred_cols = ["row_id", *STRUCTURE_METHODS]
    s = sample.merge(pairs[pair_cols], on="row_id", validate="one_to_one")
    s = s.merge(pred[pred_cols], on="row_id", validate="one_to_one")
    s = s.merge(rv_rows, on="row_id", validate="one_to_one")
    s["original_weight"] = 1 / s.groupby(["side", "delivery_date"]).row_id.transform("size")
    s["pair_available"] = True
    s["orig_credit_norm"], s["outward_credit_norm"], entry_cost, rv_sigma = structure_credits(s, "RV4H_X125")
    s["assumed_iv"] = rv_sigma
    s["credit_concession_norm"] = s.orig_credit_norm - s.outward_credit_norm
    s["original_net_loss"] = s.loss_normalized - s.orig_credit_norm + entry_cost
    s["outward_net_loss"] = s.outward_loss_normalized - s.outward_credit_norm + entry_cost
    s["actual_saving_norm"] = s.delta_normalized
    action, gain, net = structure_policy_arrays(
        s.COARSE_GEOM_CAL3M.to_numpy(float),
        s.actual_saving_norm.to_numpy(float),
        s.credit_concession_norm.to_numpy(float),
        s.original_net_loss.to_numpy(float),
        s.outward_net_loss.to_numpy(float),
        s.pair_available.to_numpy(bool),
    )
    s["COARSE_GEOM_CAL3M_net_loss"] = net
    structure_rows = [
        {
            "side": side,
            "action_count": int(action[s.side.eq(side).to_numpy()].sum()),
            "gain_mean": weighted_mean(gain[s.side.eq(side).to_numpy()], s.loc[s.side.eq(side), "original_weight"]),
            **pair_metrics(s, s.COARSE_GEOM_CAL3M_net_loss.to_numpy(float), side, "policy_net_loss"),
        }
        for side in ("put", "call")
    ]
    return {
        "schema": "astra_accounting_v16_smoke@1.0.0",
        "exit_population_rows": int(len(population)),
        "exit_sample_rows": int(len(f)),
        "structure_prediction_file": str(pred_path),
        "structure_pair_file": str(pair_path),
        "structure_rv_file": str(rv_path),
        "structure_sample_rows": int(len(s)),
        "structure_smoke_scenario": "RV4H_X125",
        "structure_smoke_assumed_iv_min": float(np.nanmin(s.assumed_iv)),
        "structure_smoke_assumed_iv_max": float(np.nanmax(s.assumed_iv)),
        "exit_summary_rows": exit_rows,
        "structure_summary_rows": structure_rows,
    }


def run(
    research: Path,
    output: str,
    resume_existing_failure: bool = False,
    append_only_existing: bool = False,
    reuse_exit_from: Path | None = None,
):
    protocol, seal, protocol_sha = validate_protocol(research)
    out = research / output
    if out.exists():
        if not append_only_existing and (not resume_existing_failure or not (out / "failure.txt").exists()):
            raise FileExistsError(f"refusing to overwrite existing output directory {out}")
        resumed = True
    else:
        out.mkdir(parents=False, exist_ok=False)
        resumed = False
    root = research.parents[1]
    try:
        if not resumed:
            identity = {
                "schema": "astra_accounting_v16_appendix@1.0.0",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "protocol_sha256": protocol_sha,
                "protocol_sealed_at_utc": seal["sealed_at_utc"],
                "scope": protocol["appendices"],
                "production_changed": False,
                "new_llm_calls": 0,
                "training_jobs": 0,
                "source_code": code_hashes(),
            }
            save_new(out / "execution_identity.json", identity)
        else:
            identity = {
                "schema": "astra_accounting_v16_appendix_recovery@1.0.0",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "protocol_sha256": protocol_sha,
                "protocol_sealed_at_utc": seal["sealed_at_utc"],
                "scope": protocol["appendices"],
                "append_only_existing_output": append_only_existing,
                "production_changed": False,
                "new_llm_calls": 0,
                "training_jobs": 0,
                "source_code": code_hashes(),
            }
            save_new(out / "execution_identity_recovery.json", identity)
        if reuse_exit_from is not None:
            exit_info = existing_exit_accounting_info(root, reuse_exit_from)
            exit_info["reused_from"] = str(reuse_exit_from)
        elif not resumed:
            exit_info = recompute_exit_accounting(root, out)
        else:
            exit_info = existing_exit_accounting_info(root, out)
        structure_info = recompute_structure_accounting(root, out)
        summary = {
            "schema": "astra_accounting_v16_appendix_summary@1.0.0",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_sha256": protocol_sha,
            "exit": exit_info,
            "structure": structure_info,
            "structure_price_scenarios": list(STRUCTURE_SCENARIOS),
            "rv4h_adaptive_structure_appendix": "expanded for structure only from run_01/base_rows.csv rv4_annualized as the sealed RV4H_X125 scenario; v15 exit accounting remains limited to the four completed legacy scenarios",
            "actual_market_ev": None,
            "natural_nr_effect": None,
            "runtime_model_changed": False,
            "production_changed": False,
            "resumed_after_preserved_failure": resumed,
            "exit_reused_from": str(reuse_exit_from) if reuse_exit_from is not None else None,
        }
        save_new(out / "summary.json", summary)
        source_manifest = {
            "schema": "astra_accounting_v16_sources@1.0.0",
            "files": {**exit_info["source_files"], **structure_info["source_files"]},
            "reused_appendix_outputs": exit_info.get("output_files", {}),
        }
        save_new(out / "source_manifest.json", source_manifest)
        receipt = write_completion_receipt(out, protocol_sha, exit_info, structure_info, resumed, reuse_exit_from)
        print(
            json.dumps(
                {"stage": "finished", "output": str(out), "summary": summary, "completion_receipt": receipt},
                ensure_ascii=False,
            )
        )
    except Exception:
        (out / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--research",
        type=Path,
        default=Path(__file__).resolve().parents[3] / ".artifacts/astra-underwriting-v16-20260922",
    )
    parser.add_argument("--output", default="appendix_01")
    parser.add_argument("--resume-existing-failure", action="store_true")
    parser.add_argument("--append-only-existing", action="store_true")
    parser.add_argument("--reuse-exit-from", type=Path)
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    if args.smoke_only:
        validate_protocol(args.research)
        print(json.dumps(smoke_summary(args.research.parents[1]), ensure_ascii=False, indent=2))
    else:
        run(
            args.research,
            args.output,
            args.resume_existing_failure,
            args.append_only_existing,
            args.reuse_exit_from,
        )


if __name__ == "__main__":
    main()
