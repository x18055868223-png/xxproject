"""L02 local residual calibration for Astra underwriting v1.6.

Research-only runner. It reuses frozen UTC08 base rows from R16/run_01 and
does not retrain the base model, call an LLM, or tune extra parameters.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

try:
    from tools import astra_underwriting_v16 as core
except ImportError:  # pragma: no cover - direct script execution from worktree root
    import astra_underwriting_v16 as core


YEARS = (2022, 2023, 2024, 2025)
SIDES = ("put", "call")
STATES = core.STATES
POLICIES = ("ORIGINAL", "GLOBAL_CAL", "LOCAL_ALL_SUPPORTED", "LOCAL_EMBED", "LOCAL_ONLY")
ELIGIBLE_CELLS = {("put", "RANGE"), ("call", "RANGE")}
Q1_GLOBAL_MIN_DAYS = 60
STATE_MIN_DAYS = 20
SHRINK_K = 30.0
MATERIAL_RESIDUAL = 0.005
PERMISSION_SCENARIOS = ("IV60", "RV4H_X125")
PRIMARY_SCENARIO = "RV4H_X125"
PRIMARY_CONFIDENCE = 0.975
SEAL_SCHEMA = "astra_underwriting_v16_L02_local_calibration@1.0.0"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_l02_seal(research: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    amendment = read_json(research / "L02_amendment.json")
    seal = read_json(research / "L02_seal.json")
    actual = core.digest(research / "L02_amendment.json")
    if seal.get("amendment_sha256") != actual:
        raise ValueError("L02 amendment hash mismatch")
    if seal.get("local_calibration_results_computed_before_seal") is not False:
        raise ValueError("L02 seal does not freeze a pre-result amendment")
    if amendment.get("schema") != "astra_underwriting_v16_L02_amendment@1.0.0":
        raise ValueError("unexpected L02 amendment schema")
    return amendment, seal


def prepare_utc08(base_rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = core.schedule(base_rows, "UTC08")
    frame = frame.copy()
    frame["split_year"] = pd.to_datetime(frame.entry_ms, unit="ms", utc=True).dt.year.astype(int)
    frame["split"] = "outside_years"
    frame.loc[~frame.split_year.isin(YEARS), "split"] = "outside_years"
    for year in YEARS:
        jan1 = utc_ms(year, 1, 1)
        apr1 = utc_ms(year, 4, 1)
        jul1 = utc_ms(year, 7, 1)
        nxt = utc_ms(year + 1, 1, 1)
        entry = frame.entry_ms.to_numpy()
        expiry = frame.expiry_ms.to_numpy()
        in_year = frame.split_year.eq(year).to_numpy()
        q1_entry = in_year & (entry >= jan1) & (entry < apr1)
        q2_entry = in_year & (entry >= apr1) & (entry < jul1)
        h2_entry = in_year & (entry >= jul1) & (entry < nxt)
        frame.loc[q1_entry & (expiry < apr1), "split"] = "Q1"
        frame.loc[q2_entry & (expiry < jul1), "split"] = "Q2"
        frame.loc[h2_entry & (expiry < nxt), "split"] = "H2"
        frame.loc[(q1_entry & (expiry >= apr1)) | (q2_entry & (expiry >= jul1)) | (h2_entry & (expiry >= nxt)), "split"] = (
            "boundary_maturity_gap"
        )
    receipt = {
        "schedule": "UTC08",
        "rows": int(len(frame)),
        "delivery_days": int(frame.delivery_date.nunique()),
        "split_counts": frame.split.value_counts().to_dict(),
        "boundary_maturity_gap_rows": int(frame.split.eq("boundary_maturity_gap").sum()),
    }
    return frame, receipt


def fit_q1_parameters(frame: pd.DataFrame) -> pd.DataFrame:
    q1 = frame.loc[frame.split.eq("Q1")].copy()
    q1["residual"] = q1.loss_normalized - q1.mu_stat
    records: list[dict[str, Any]] = []
    for year in YEARS:
        for side in SIDES:
            side_q1 = q1.loc[(q1.split_year == year) & q1.side.eq(side)]
            global_days = int(side_q1.delivery_date.nunique())
            global_supported = global_days >= Q1_GLOBAL_MIN_DAYS
            b = core.weighted_mean(side_q1.residual, side_q1.original_weight) if global_supported else 0.0
            for state in STATES:
                state_q1 = side_q1.loc[side_q1.regime.eq(state)]
                state_days = int(state_q1.delivery_date.nunique())
                state_supported = state_days >= STATE_MIN_DAYS
                state_mean = core.weighted_mean(state_q1.residual, state_q1.original_weight) if len(state_q1) else np.nan
                r_raw = float(state_mean - b) if state_supported and np.isfinite(state_mean) else np.nan
                shrink_lambda = float(state_days / (state_days + SHRINK_K)) if state_supported else 0.0
                records.append(
                    {
                        "year": year,
                        "side": side,
                        "state": state,
                        "eligible_cell": (side, state) in ELIGIBLE_CELLS,
                        "q1_global_rows": int(len(side_q1)),
                        "q1_global_days": global_days,
                        "q1_global_supported": bool(global_supported),
                        "b_global": float(b),
                        "q1_state_rows": int(len(state_q1)),
                        "q1_state_days": state_days,
                        "q1_state_supported": bool(state_supported),
                        "q1_state_residual_mean": state_mean,
                        "r_raw": r_raw,
                        "lambda": shrink_lambda,
                        "local_adjustment": float(shrink_lambda * r_raw) if np.isfinite(r_raw) else 0.0,
                    }
                )
    return pd.DataFrame(records)


def attach_predictions(frame: pd.DataFrame, params: pd.DataFrame, permissions: pd.DataFrame | None = None) -> pd.DataFrame:
    result = frame.copy()
    p = params.set_index(["year", "side", "state"])
    global_params = (
        params.sort_values(["year", "side", "state"])
        .drop_duplicates(["year", "side"])
        .set_index(["year", "side"])
    )
    allowed = set()
    if permissions is not None and len(permissions):
        allowed = set(
            tuple(x)
            for x in permissions.loc[permissions.allowed, ["year", "side", "state"]].itertuples(index=False, name=None)
        )
    mu_global, mu_all, mu_embed = [], [], []
    all_supported, embed_allowed = [], []
    global_supported, state_supported = [], []
    for row in result.itertuples(index=False):
        year_side = (int(row.split_year), row.side)
        state_key = (int(row.split_year), row.side, row.regime)
        global_rec = global_params.loc[year_side] if year_side in global_params.index else None
        rec = p.loc[state_key] if state_key in p.index else None
        if global_rec is None:
            b = 0.0
            is_global_supported = False
        else:
            b = float(global_rec.b_global)
            is_global_supported = bool(global_rec.q1_global_supported)
        if rec is None:
            local_adj = 0.0
            is_eligible = False
            is_state_supported = False
        else:
            local_adj = float(rec.local_adjustment)
            is_eligible = bool(rec.eligible_cell)
            is_state_supported = bool(rec.q1_state_supported)
        g = lower_bound_prediction(row.mu_stat, b)
        use_all = is_global_supported and is_eligible and is_state_supported
        use_embed = (int(row.split_year), row.side, row.regime) in allowed
        local = lower_bound_prediction(row.mu_stat, b + local_adj)
        mu_global.append(g)
        mu_all.append(local if use_all else g)
        mu_embed.append(local if use_embed else g)
        all_supported.append(use_all)
        embed_allowed.append(use_embed)
        global_supported.append(is_global_supported)
        state_supported.append(is_state_supported)
    result["mu_global"] = mu_global
    result["mu_local_all_supported"] = mu_all
    result["mu_local_embed"] = mu_embed
    result["local_all_supported_cell"] = all_supported
    result["local_embed_allowed_cell"] = embed_allowed
    result["q1_global_supported"] = global_supported
    result["q1_state_supported"] = state_supported
    return result


def lower_bound_prediction(mu: Any, adjustment: float) -> float:
    value = float(mu) + float(adjustment)
    return max(0.0, value) if np.isfinite(value) else np.nan


def select_permissions(frame: pd.DataFrame, params: pd.DataFrame) -> pd.DataFrame:
    q2 = attach_predictions(frame.loc[frame.split.eq("Q2")], params, None)
    records: list[dict[str, Any]] = []
    p = params.set_index(["year", "side", "state"])
    for year in YEARS:
        for side in SIDES:
            side_q2 = q2.loc[(q2.split_year == year) & q2.side.eq(side)].copy()
            for state in STATES:
                rec = p.loc[(year, side, state)]
                state_q2 = side_q2.loc[side_q2.regime.eq(state)]
                q2_days = int(state_q2.delivery_date.nunique())
                q2_resid = core.weighted_mean(
                    state_q2.loss_normalized - state_q2.mu_global,
                    state_q2.original_weight,
                ) if len(state_q2) else np.nan
                same_direction = bool(np.isfinite(rec.r_raw) and np.isfinite(q2_resid) and rec.r_raw * q2_resid > 0)
                price_checks = {}
                for scenario in PERMISSION_SCENARIOS:
                    price_checks[scenario] = q2_price_permission(side_q2, state, scenario)
                base_conditions = {
                    "eligible_cell": bool(rec.eligible_cell),
                    "q1_global_supported": bool(rec.q1_global_supported),
                    "q1_state_supported": bool(rec.q1_state_supported),
                    "q2_state_supported": q2_days >= STATE_MIN_DAYS,
                    "q1_abs_residual": bool(np.isfinite(rec.r_raw) and abs(float(rec.r_raw)) >= MATERIAL_RESIDUAL),
                    "q2_abs_residual": bool(np.isfinite(q2_resid) and abs(float(q2_resid)) >= MATERIAL_RESIDUAL),
                    "same_direction": same_direction,
                }
                price_pass = all(
                    item["mean"] is not None
                    and item["mean"] > 0
                    and item["drop2_best_mean"] is not None
                    and item["drop2_best_mean"] > 0
                    and item["positive_7d_blocks"] >= 3
                    for item in price_checks.values()
                )
                allowed = all(base_conditions.values()) and price_pass
                records.append(
                    {
                        "year": year,
                        "side": side,
                        "state": state,
                        "eligible_cell": bool(rec.eligible_cell),
                        "q1_global_days": int(rec.q1_global_days),
                        "q1_state_days": int(rec.q1_state_days),
                        "q2_state_days": q2_days,
                        "b_global": float(rec.b_global),
                        "r_raw": float(rec.r_raw) if np.isfinite(rec.r_raw) else np.nan,
                        "lambda": float(rec["lambda"]),
                        "local_adjustment": float(rec.local_adjustment),
                        "q2_residual_after_global": q2_resid,
                        "same_direction": same_direction,
                        "conditions": base_conditions,
                        "price_checks": price_checks,
                        "price_pass": bool(price_pass),
                        "allowed": bool(allowed),
                    }
                )
    return pd.DataFrame(records)


def q2_price_permission(side_q2: pd.DataFrame, state: str, scenario: str) -> dict[str, Any]:
    if not len(side_q2):
        return {"mean": None, "drop2_best_mean": None, "positive_7d_blocks": 0, "days": 0}
    credit, cost, _sigma = core.scenario_quote(side_q2, scenario)
    _, _action_g, pnl_g, _one_g = core.policy_metrics(side_q2, credit, cost, side_q2.mu_global.to_numpy(float))
    _, _action_l, pnl_l, _one_l = core.policy_metrics(side_q2, credit, cost, side_q2.mu_local_all_supported.to_numpy(float))
    delta = np.where(side_q2.regime.eq(state).to_numpy(), pnl_l - pnl_g, 0.0)
    daily = core.daily_sum(side_q2, delta).dropna().sort_index()
    if not len(daily):
        return {"mean": None, "drop2_best_mean": None, "positive_7d_blocks": 0, "days": 0}
    drop2 = daily.sort_values().iloc[:-2].mean() if len(daily) > 2 else np.nan
    dates = pd.to_datetime(daily.index)
    blocks = ((dates - pd.Timestamp("1970-01-01")).days // 7).to_numpy()
    block_sum = pd.Series(daily.to_numpy(), index=blocks).groupby(level=0).sum()
    return {
        "mean": float(daily.mean()),
        "drop2_best_mean": float(drop2) if np.isfinite(drop2) else None,
        "positive_7d_blocks": int((block_sum > 0).sum()),
        "days": int(len(daily)),
        "state_rows": int(side_q2.regime.eq(state).sum()),
        "state_days": int(side_q2.loc[side_q2.regime.eq(state), "delivery_date"].nunique()),
    }


def evaluate_h2(frame: pd.DataFrame, params: pd.DataFrame, permissions: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    h2 = attach_predictions(frame.loc[frame.split.eq("H2")], params, permissions)
    ledgers: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    primary: dict[str, Any] = {}
    for scenario in core.SCENARIOS:
        for side in SIDES:
            g = h2.loc[h2.side.eq(side)].copy()
            if not len(g):
                continue
            credit, cost, sigma = core.scenario_quote(g, scenario)
            base = g[
                [
                    "row_id",
                    "side",
                    "delivery_date",
                    "as_of_ms",
                    "split_year",
                    "original_weight",
                    "regime",
                    "loss_normalized",
                    "mu_stat",
                    "mu_global",
                    "mu_local_all_supported",
                    "mu_local_embed",
                    "local_all_supported_cell",
                    "local_embed_allowed_cell",
                ]
            ].copy()
            base["scenario"] = scenario
            base["credit"] = credit
            base["cost"] = cost
            base["assumed_iv"] = sigma
            policy_values: dict[str, np.ndarray] = {}
            policy_mus = {
                "ORIGINAL": g.mu_stat.to_numpy(float),
                "GLOBAL_CAL": g.mu_global.to_numpy(float),
                "LOCAL_ALL_SUPPORTED": g.mu_local_all_supported.to_numpy(float),
                "LOCAL_EMBED": g.mu_local_embed.to_numpy(float),
                "LOCAL_ONLY": g.mu_local_embed.to_numpy(float),
            }
            for policy in POLICIES:
                eligible = g.local_embed_allowed_cell.to_numpy(bool) if policy == "LOCAL_ONLY" else None
                metric, action, pnl, one = core.policy_metrics(g, credit, cost, policy_mus[policy], eligible=eligible)
                pred = policy_mus[policy]
                mse_mask = np.isfinite(pred)
                metric = dict(metric)
                metric.update(
                    {
                        "policy": policy,
                        "scenario": scenario,
                        "side": side,
                        "rows": int(len(g)),
                        "days": int(g.delivery_date.nunique()),
                        "mse_loss_prediction": core.weighted_mean(
                            (g.loss_normalized.to_numpy(float)[mse_mask] - pred[mse_mask]) ** 2,
                            g.original_weight.to_numpy(float)[mse_mask],
                        ),
                        "prediction_min": float(np.nanmin(pred)),
                        "loss_above_one_rows": int(g.loss_normalized.gt(1).sum()),
                        "sparse_fallback_weight_fraction": sparse_fallback_weight(g, policy),
                    }
                )
                summaries.append(metric)
                policy_values[policy] = pnl
                base[f"{policy}_prediction"] = pred
                base[f"{policy}_action"] = action
                base[f"{policy}_pnl"] = pnl
                base[f"{policy}_one_trade_net"] = one
            for policy in ("ORIGINAL", "LOCAL_ALL_SUPPORTED", "LOCAL_EMBED", "LOCAL_ONLY"):
                diff = policy_values[policy] - policy_values["GLOBAL_CAL"]
                confidence = PRIMARY_CONFIDENCE if scenario == PRIMARY_SCENARIO and policy == "LOCAL_EMBED" else 0.95
                summaries.append(
                    {
                        "scenario": scenario,
                        "side": side,
                        "policy": f"{policy}-GLOBAL_CAL",
                        "contrast": core.contrast(g, diff, confidence=confidence),
                    }
                )
                if scenario == PRIMARY_SCENARIO and policy == "LOCAL_EMBED":
                    primary[side] = core.contrast(g, diff, confidence=PRIMARY_CONFIDENCE)
            ledgers.append(base)
    return pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame(), summaries, primary


def sparse_fallback_weight(frame: pd.DataFrame, policy: str) -> float:
    w = frame.original_weight.to_numpy(float)
    if not len(frame) or not np.isfinite(w).any() or w.sum() <= 0:
        return np.nan
    eligible = np.array([(side, state) in ELIGIBLE_CELLS for side, state in zip(frame.side, frame.regime)])
    if policy == "LOCAL_ALL_SUPPORTED":
        fallback = eligible & ~frame.local_all_supported_cell.to_numpy(bool)
    elif policy in {"LOCAL_EMBED", "LOCAL_ONLY"}:
        fallback = eligible & ~frame.local_embed_allowed_cell.to_numpy(bool)
    else:
        fallback = np.zeros(len(frame), dtype=bool)
    return float(w[fallback].sum() / w.sum())


def run(root: str | Path, research: str | Path, run_name: str = "local_01") -> dict[str, Any]:
    root = Path(root)
    research = Path(research)
    amendment, seal = verify_l02_seal(research)
    out = research / run_name
    out.mkdir(exist_ok=False)
    core.save(out / "run_started.json", {"utc": datetime.now(timezone.utc).isoformat(), "seal": seal})
    try:
        base_path = research / "run_01" / "base_rows.csv"
        base = pd.read_csv(base_path)
        frame, split_receipt = prepare_utc08(base)
        params = fit_q1_parameters(frame)
        permissions = select_permissions(frame, params)
        ledger, summaries, primary = evaluate_h2(frame, params, permissions)
        params.to_csv(out / "calibration_params.csv", index=False)
        permissions.to_csv(out / "permission_support.csv", index=False)
        core.save(out / "calibration_params.json", params.to_dict("records"))
        core.save(out / "permission_support.json", permissions.to_dict("records"))
        ledger.to_csv(out / "h2_policy_ledger.csv", index=False)
        pd.DataFrame([flatten_summary(x) for x in summaries]).to_csv(out / "policy_summary.csv", index=False)
        core.save(out / "policy_summary.json", summaries)
        allowed = permissions.loc[permissions.allowed, ["year", "side", "state"]].to_dict("records")
        core.save(out / "allowed_cells.json", {"allowed": allowed, "allowed_count": len(allowed), "eligible_cells": sorted(map(list, ELIGIBLE_CELLS))})
        core.save(out / "primary_contrasts.json", primary)
        receipt = {
            "schema": SEAL_SCHEMA,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "amendment_sha256": seal["amendment_sha256"],
            "base_rows_sha256": core.digest(base_path),
            "split_receipt": split_receipt,
            "q1_parameter_rows": int(len(params)),
            "permission_rows": int(len(permissions)),
            "allowed_count": int(len(allowed)),
            "h2_ledger_rows": int(len(ledger)),
            "new_training_jobs": 0,
            "base_model_retraining_jobs": 0,
            "residual_calibration_side_years": len(YEARS) * len(SIDES),
            "llm_calls": 0,
            "production_changed": False,
        }
        core.save(out / "receipt.json", receipt)
        command = (
            f'"{sys.executable}" "{Path(__file__).resolve()}" '
            f'--root "{root}" --research "{research}" --run-name {run_name}'
        )
        (out / "reproduce_command.txt").write_text(
            command
            + "\n\n"
            + "Existing run directories are refused; use a fresh --run-name such as local_03.\n",
            encoding="utf-8",
        )
        return receipt
    except Exception as exc:
        import traceback

        core.save(out / "failure.json", {"error": str(exc), "traceback": traceback.format_exc()})
        raise


def flatten_summary(item: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                if isinstance(subvalue, dict):
                    flat[f"{key}_{subkey}"] = json.dumps(core.safe(subvalue), sort_keys=True, ensure_ascii=False)
                else:
                    flat[f"{key}_{subkey}"] = subvalue
        else:
            flat[key] = value
    return flat


def utc_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--research", required=True)
    parser.add_argument("--run-name", default="local_01")
    args = parser.parse_args()
    run(args.root, args.research, args.run_name)


if __name__ == "__main__":
    main()
