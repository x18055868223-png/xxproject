"""Astra v1.3 opportunity-quality diagnostics.

This module is intentionally read-only with respect to the sealed v1.1
research inputs. It ranks Put and Call rows separately by already-saved
expected-loss predictions, then reports a retrospective equal-coverage ranking
diagnostic. The outputs are descriptive research artifacts, not thresholds,
policy replay, or economic edge evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

SCHEMA = "astra_entry_quality@1.0.0"
ROW_SCHEMA = "astra_entry_quality_rows@1.0.0"
DAILY_SCHEMA = "astra_entry_quality_daily@1.0.0"

CANDIDATE_FILES = {
    "geometry": "gam__geometry__C0.1__A0.1.csv",
    "statistical": "catboost__statistical__D4.csv",
    "joint": "catboost__joint__D3.csv",
}

PREDICTION_COLUMNS = [
    "candidate_id",
    "fold",
    "row_id",
    "observation_id",
    "as_of_ms",
    "entry_ms",
    "expiry_ms",
    "delivery_date",
    "side",
    "target_width",
    "actual_width",
    "expected_loss_normalized",
    "probability_positive",
    "breach_probability",
    "actual_loss_normalized",
    "actual_payout_btc",
    "protection_breached",
]

MODEL_INPUT_COLUMNS = [
    "row_id",
    "observation_id",
    "as_of_ms",
    "entry_ms",
    "expiry_ms",
    "delivery_date",
    "side",
    "target_width",
    "actual_width",
    "entry_price",
    "dte_hours",
    "short_distance_fraction",
    "width_fraction",
    "vol_240",
    "loss_normalized",
    "payout_btc",
    "short_leg_breached",
    "protection_leg_breached",
]


def digest_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def canonical_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"put", "put_credit", "bull_put"}:
        return "put_credit"
    if text in {"call", "call_credit", "bear_call"}:
        return "call_credit"
    raise ValueError(f"unknown side: {value!r}")


def bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "breached"}:
        return True
    if text in {"0", "false", "no", "n", "not_breached"}:
        return False
    if text in {"", "nan", "none", "null"}:
        return None
    raise ValueError(f"unknown boolean value: {value!r}")


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return datetime(year, month, min(dt.day, 28), tzinfo=timezone.utc)


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    total = 0.0
    acc = 0.0
    for value, weight in zip(values, weights):
        v = float(value)
        w = float(weight)
        if math.isfinite(v) and math.isfinite(w) and w > 0:
            acc += v * w
            total += w
    return acc / total if total > 0 else None


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    pairs = sorted(
        (float(v), float(w))
        for v, w in zip(values, weights)
        if math.isfinite(float(v)) and math.isfinite(float(w)) and float(w) > 0
    )
    if not pairs:
        raise ValueError("weighted_quantile_unavailable")
    total = sum(w for _, w in pairs)
    cutoff = q * total
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= cutoff - 1e-15:
            return value
    return pairs[-1][0]


def weighted_es95(values: Sequence[float], weights: Sequence[float]) -> float | None:
    pairs = sorted(
        (float(v), float(w))
        for v, w in zip(values, weights)
        if math.isfinite(float(v)) and math.isfinite(float(w)) and float(w) > 0
    )
    if not pairs:
        return None
    total = sum(weight for _, weight in pairs)
    need = 0.05 * total
    if need <= 0:
        return None
    remaining = need
    acc = 0.0
    for value, weight in reversed(pairs):
        take = min(weight, remaining)
        acc += value * take
        remaining -= take
        if remaining <= 1e-15:
            break
    return acc / need


def side_day_weights(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series([], dtype=float, index=frame.index)
    sides = frame["side"].map(canonical_side)
    day_counts = frame.groupby([sides, "delivery_date"])["row_id"].transform("size").astype(float)
    days_per_side = frame.assign(_side=sides).groupby("_side")["delivery_date"].nunique().to_dict()
    weights = []
    for idx, side in zip(frame.index, sides):
        days = float(days_per_side[side])
        weights.append(1.0 / days / float(day_counts.loc[idx]))
    return pd.Series(weights, index=frame.index, dtype=float)


def fixed_bin(value: Any, edges: Sequence[float], precision: int = 6) -> str:
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return "missing"
    f = float(v)
    ordered = [float(edge) for edge in edges]
    if f <= ordered[0]:
        return f"(-inf,{ordered[0]:.{precision}g}]"
    for lo, hi in zip(ordered, ordered[1:]):
        if lo < f <= hi:
            return f"({lo:.{precision}g},{hi:.{precision}g}]"
    return f"({ordered[-1]:.{precision}g},inf)"


def risk_bin(value: Any, edges: Sequence[float]) -> str:
    v = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(v):
        return "missing"
    f = float(v)
    ordered = [float(edge) for edge in edges]
    if f < ordered[0]:
        return f"under_{ordered[0]:.6g}"
    for lo, hi in zip(ordered, ordered[1:]):
        if lo <= f < hi:
            return f"[{lo:.6g},{hi:.6g})"
    return f"[{ordered[-1]:.6g},inf)"


def _date_from_delivery(value: Any) -> datetime:
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def utc_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def compute_fit_vol_medians(source_rows: pd.DataFrame, years: Sequence[int]) -> dict[tuple[int, str], float]:
    rows = source_rows.copy()
    rows["side"] = rows["side"].map(canonical_side)
    rows["as_of_ms"] = pd.to_numeric(rows["as_of_ms"], errors="coerce")
    rows["expiry_ms"] = pd.to_numeric(rows["expiry_ms"], errors="coerce")
    rows["vol_240"] = pd.to_numeric(rows["vol_240"], errors="coerce")
    result: dict[tuple[int, str], float] = {}
    for year in years:
        fit_start_ms = utc_ms(int(year) - 2, 1, 1)
        fit_end_ms = utc_ms(int(year) - 1, 10, 1)
        for side in ("put_credit", "call_credit"):
            part = rows[
                (rows["side"] == side)
                & (rows["as_of_ms"] >= fit_start_ms)
                & (rows["as_of_ms"] < fit_end_ms)
                & (rows["expiry_ms"] < fit_end_ms)
                & rows["vol_240"].notna()
            ].copy()
            if part.empty:
                raise ValueError(f"fit_vol_median_unavailable:{year}:{side}")
            part["row_weight"] = side_day_weights(part)
            result[(int(year), side)] = weighted_quantile(part["vol_240"], part["row_weight"], 0.5)
    return result


def add_opportunity_strata(rows: pd.DataFrame, protocol: Mapping[str, Any], fit_vol_medians: Mapping[tuple[int, str], float]) -> pd.DataFrame:
    out = rows.copy()
    opportunity = protocol["opportunity"]
    dte_edges = opportunity["dte_edges"]
    distance_edges = opportunity["distance_edges"]
    out["side"] = out["side"].map(canonical_side)
    out["year"] = out["delivery_date"].str[:4].astype(int)
    out["actual_width_num"] = pd.to_numeric(out["actual_width"], errors="coerce")
    out["dte_hours"] = pd.to_numeric(out["dte_hours"], errors="coerce")
    out["short_distance_fraction"] = pd.to_numeric(out["short_distance_fraction"], errors="coerce")
    out["vol_240"] = pd.to_numeric(out["vol_240"], errors="coerce")
    out["dte_bin"] = out["dte_hours"].map(lambda value: fixed_bin(value, dte_edges, precision=4))
    out["short_distance_bin"] = out["short_distance_fraction"].map(lambda value: fixed_bin(value, distance_edges, precision=6))
    out["vol_fit_bin"] = [
        vol_fit_label(vol, int(year), side, fit_vol_medians)
        for vol, year, side in zip(out["vol_240"], out["year"], out["side"])
    ]
    out["stratum_key"] = (
        out["year"].astype(str)
        + "|"
        + out["side"].astype(str)
        + "|w="
        + out["actual_width_num"].map(lambda value: f"{float(value):.12g}")
        + "|dte="
        + out["dte_bin"].astype(str)
        + "|dist="
        + out["short_distance_bin"].astype(str)
        + "|vol="
        + out["vol_fit_bin"].astype(str)
    )
    return out


def vol_fit_label(value: Any, year: int, side: str, fit_vol_medians: Mapping[tuple[int, str], float]) -> str:
    vol = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(vol):
        return "vol_missing"
    return "vol_low_or_equal" if float(vol) <= float(fit_vol_medians[(int(year), side)]) else "vol_high"


def allocate_fractional_coverage(
    frame: pd.DataFrame,
    score_col: str,
    coverage: float,
    *,
    min_stratum_days: int,
    stratum_col: str = "stratum_key",
    weight_col: str = "row_weight",
) -> tuple[pd.Series, list[dict[str, Any]]]:
    if not 0 <= float(coverage) <= 1:
        raise ValueError(f"coverage_out_of_range:{coverage}")
    membership = pd.Series(0.0, index=frame.index, dtype=float)
    meta: list[dict[str, Any]] = []
    for stratum, part in frame.groupby(stratum_col, sort=True):
        weights = pd.to_numeric(part[weight_col], errors="coerce").astype(float)
        total_weight = float(weights.sum())
        days = int(part["delivery_date"].nunique())
        target = float(coverage) * total_weight
        if total_weight <= 0:
            raise ValueError(f"empty_weight_stratum:{stratum}")
        if days < int(min_stratum_days):
            membership.loc[part.index] = float(coverage)
            meta.append(
                {
                    "stratum_key": str(stratum),
                    "days": days,
                    "rows": int(len(part)),
                    "total_weight": total_weight,
                    "selected_weight": target,
                    "coverage": float(coverage),
                    "sparse_uniform": True,
                    "partial_tie_groups": 0,
                }
            )
            continue
        scores = pd.to_numeric(part[score_col], errors="coerce")
        if scores.isna().any():
            raise ValueError(f"missing_score_in_informative_stratum:{stratum}:{score_col}")
        scores_arr = scores.to_numpy(dtype=float)
        weights_arr = weights.to_numpy(dtype=float)
        order = np.argsort(scores_arr, kind="mergesort")
        sorted_scores = scores_arr[order]
        sorted_weights = weights_arr[order]
        boundaries = np.r_[0, np.flatnonzero(np.diff(sorted_scores) != 0) + 1, len(sorted_scores)]
        part_membership = np.zeros(len(part), dtype=float)
        selected_weight = 0.0
        partial_tie_groups = 0
        for start, end in zip(boundaries[:-1], boundaries[1:]):
            group_weight = float(sorted_weights[start:end].sum())
            remaining = target - selected_weight
            if remaining <= 1e-15:
                fraction = 0.0
            elif remaining >= group_weight - 1e-15:
                fraction = 1.0
            else:
                fraction = max(0.0, min(1.0, remaining / group_weight))
                partial_tie_groups += 1
            part_membership[order[start:end]] = fraction
            selected_weight += group_weight * fraction
        membership.loc[part.index] = part_membership
        meta.append(
            {
                "stratum_key": str(stratum),
                "days": days,
                "rows": int(len(part)),
                "total_weight": total_weight,
                "selected_weight": float((membership.loc[part.index] * weights).sum()),
                "coverage": float(coverage),
                "sparse_uniform": False,
                "partial_tie_groups": partial_tie_groups,
            }
        )
    return membership, meta


def _fractional_day_equal_mean(frame: pd.DataFrame, effective_weight: pd.Series, value_col: str) -> float | None:
    rows = frame.copy()
    rows["_effective_weight"] = effective_weight
    rows = rows[rows["_effective_weight"] > 0]
    if rows.empty:
        return None
    day_values = []
    for _, part in rows.groupby("delivery_date", sort=True):
        day_values.append(float((part[value_col] * part["_effective_weight"]).sum() / part["_effective_weight"].sum()))
    return float(sum(day_values) / len(day_values)) if day_values else None


def membership_metrics(frame: pd.DataFrame, membership: pd.Series) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0}
    actual = pd.to_numeric(frame["actual_loss_normalized"], errors="coerce").astype(float)
    base_weight = pd.to_numeric(frame["row_weight"], errors="coerce").astype(float)
    selected_weight = base_weight * membership.astype(float)
    deferred_weight = base_weight * (1.0 - membership.astype(float))
    total_weight = float(base_weight.sum())
    sel_weight_sum = float(selected_weight.sum())
    def_weight_sum = float(deferred_weight.sum())
    prot = frame["protection_breached_bool"].astype(bool)
    partial = (actual > 0) & (~prot)
    zero = actual <= 0

    selected_loss_sum = float((actual * selected_weight).sum())
    deferred_loss_sum = float((actual * deferred_weight).sum())
    selected_mean = selected_loss_sum / sel_weight_sum if sel_weight_sum > 0 else None
    deferred_mean = deferred_loss_sum / def_weight_sum if def_weight_sum > 0 else None
    partial_contribution = float((actual[partial] * selected_weight[partial]).sum()) / sel_weight_sum if sel_weight_sum > 0 else None
    protection_contribution = float((actual[prot] * selected_weight[prot]).sum()) / sel_weight_sum if sel_weight_sum > 0 else None
    if partial_contribution is not None and protection_contribution is not None and selected_mean is not None:
        contribution_residual = selected_mean - partial_contribution - protection_contribution
    else:
        contribution_residual = None
    original_mean = float((actual * base_weight).sum() / total_weight) if total_weight > 0 else None
    return {
        "rows": int(len(frame)),
        "delivery_days": int(frame["delivery_date"].nunique()),
        "total_original_weight": total_weight,
        "selected_weight": sel_weight_sum,
        "deferred_weight": def_weight_sum,
        "coverage": sel_weight_sum / total_weight if total_weight > 0 else None,
        "conditional_mean_loss_selected": selected_mean,
        "conditional_mean_loss_deferred": deferred_mean,
        "selected_day_equal_mean_loss": _fractional_day_equal_mean(frame, selected_weight, "actual_loss_normalized"),
        "deferred_day_equal_mean_loss": _fractional_day_equal_mean(frame, deferred_weight, "actual_loss_normalized"),
        "unconditional_selected_loss_per_original": selected_loss_sum / total_weight if total_weight > 0 else None,
        "unconditional_deferred_loss_per_original": deferred_loss_sum / total_weight if total_weight > 0 else None,
        "original_mean_loss": original_mean,
        "selected_positive_payout_rate": float(((actual > 0) * selected_weight).sum() / sel_weight_sum) if sel_weight_sum > 0 else None,
        "deferred_positive_payout_rate": float(((actual > 0) * deferred_weight).sum() / def_weight_sum) if def_weight_sum > 0 else None,
        "selected_es95_uncapped": weighted_es95(actual, selected_weight),
        "selected_partial_loss_contribution": partial_contribution,
        "selected_protection_loss_contribution": protection_contribution,
        "selected_partial_plus_protection_residual": contribution_residual,
        "deferred_zero_payout_weight": float((deferred_weight[zero]).sum()),
        "deferred_zero_payout_fraction_of_deferred": float((deferred_weight[zero]).sum() / def_weight_sum) if def_weight_sum > 0 else None,
        "deferred_zero_payout_fraction_of_original": float((deferred_weight[zero]).sum() / total_weight) if total_weight > 0 else None,
        "weight_conservation_residual": total_weight - sel_weight_sum - def_weight_sum,
        "loss_conservation_residual": original_mean - (selected_loss_sum + deferred_loss_sum) / total_weight if total_weight > 0 and original_mean is not None else None,
    }


def daily_sufficient_stats(frame: pd.DataFrame, method: str, coverage: float, membership: pd.Series) -> list[dict[str, Any]]:
    rows = frame.copy()
    rows["_membership"] = membership.astype(float)
    rows["_selected_weight"] = rows["row_weight"] * rows["_membership"]
    rows["_deferred_weight"] = rows["row_weight"] * (1.0 - rows["_membership"])
    rows["_actual"] = pd.to_numeric(rows["actual_loss_normalized"], errors="coerce").astype(float)
    rows["_positive"] = rows["_actual"] > 0
    rows["_protection"] = rows["protection_breached_bool"].astype(bool)
    rows["_partial"] = rows["_positive"] & (~rows["_protection"])
    out: list[dict[str, Any]] = []
    for (side, day), part in rows.groupby(["side", "delivery_date"], sort=True):
        selected_weight = float(part["_selected_weight"].sum())
        deferred_weight = float(part["_deferred_weight"].sum())
        selected_loss = float((part["_actual"] * part["_selected_weight"]).sum())
        deferred_loss = float((part["_actual"] * part["_deferred_weight"]).sum())
        out.append(
            {
                "schema": DAILY_SCHEMA,
                "method": method,
                "coverage": float(coverage),
                "side": side,
                "delivery_date": day,
                "row_count": int(len(part)),
                "original_weight": float(part["row_weight"].sum()),
                "selected_weight": selected_weight,
                "deferred_weight": deferred_weight,
                "selected_loss_sum": selected_loss,
                "deferred_loss_sum": deferred_loss,
                "selected_mean_loss": selected_loss / selected_weight if selected_weight > 0 else None,
                "deferred_mean_loss": deferred_loss / deferred_weight if deferred_weight > 0 else None,
                "selected_positive_weight": float((part["_positive"] * part["_selected_weight"]).sum()),
                "deferred_zero_payout_weight": float(((~part["_positive"]) * part["_deferred_weight"]).sum()),
                "selected_partial_loss_sum": float((part["_actual"] * part["_selected_weight"] * part["_partial"]).sum()),
                "selected_protection_loss_sum": float((part["_actual"] * part["_selected_weight"] * part["_protection"]).sum()),
            }
        )
    return out


def risk_display_bins(frame: pd.DataFrame, score_col: str, edges: Sequence[float]) -> list[dict[str, Any]]:
    rows = frame.copy()
    rows["_risk_bin"] = rows[score_col].map(lambda value: risk_bin(value, edges))
    rows["_actual"] = pd.to_numeric(rows["actual_loss_normalized"], errors="coerce").astype(float)
    out = []
    for label, part in rows.groupby("_risk_bin", sort=True):
        weights = part["row_weight"].astype(float)
        out.append(
            {
                "bin": str(label),
                "rows": int(len(part)),
                "delivery_days": int(part["delivery_date"].nunique()),
                "weight": float(weights.sum()),
                "score_mean": weighted_mean(part[score_col], weights),
                "actual_mean_loss": weighted_mean(part["_actual"], weights),
                "positive_rate": weighted_mean((part["_actual"] > 0).astype(float), weights),
            }
        )
    return out


def daily_method_stats(daily_rows: Sequence[Mapping[str, Any]], method: str, coverage: float, side: str) -> dict[str, dict[str, float]]:
    result = {}
    for row in daily_rows:
        if row["method"] == method and float(row["coverage"]) == float(coverage) and row["side"] == side:
            selected_weight = float(row.get("selected_weight") or 0.0)
            selected_loss_sum = float(row.get("selected_loss_sum") or 0.0)
            result[str(row["delivery_date"])] = {
                "selected_loss_sum": selected_loss_sum,
                "selected_weight": selected_weight,
                "selected_mean_loss": selected_loss_sum / selected_weight if selected_weight > 0 else None,
            }
    return result


def aggregate_selected_mean(stats: Mapping[str, Mapping[str, float]], days: Iterable[str]) -> float | None:
    loss = 0.0
    weight = 0.0
    for day in days:
        item = stats.get(str(day))
        if not item:
            continue
        loss += float(item["selected_loss_sum"])
        weight += float(item["selected_weight"])
    return loss / weight if weight > 0 else None


def selected_mean_difference_for_days(
    stat: Mapping[str, Mapping[str, float]],
    geom: Mapping[str, Mapping[str, float]],
    days: Iterable[str],
) -> tuple[float | None, float | None, float | None, float | None]:
    day_list = [str(day) for day in days]
    stat_mean = aggregate_selected_mean(stat, day_list)
    geom_mean = aggregate_selected_mean(geom, day_list)
    diff = stat_mean - geom_mean if stat_mean is not None and geom_mean is not None else None
    ratio = stat_mean / geom_mean if stat_mean is not None and geom_mean is not None and abs(geom_mean) > 1e-15 else None
    return stat_mean, geom_mean, diff, ratio


def leave_one_contributions(
    stat: Mapping[str, Mapping[str, float]],
    geom: Mapping[str, Mapping[str, float]],
    days: Sequence[str],
    full_difference: float,
) -> list[dict[str, Any]]:
    out = []
    day_set = list(days)
    for day in day_set:
        remaining = [item for item in day_set if item != day]
        _, _, leave_diff, _ = selected_mean_difference_for_days(stat, geom, remaining)
        contribution = full_difference - leave_diff if leave_diff is not None else None
        stat_day = stat.get(day, {})
        geom_day = geom.get(day, {})
        stat_daily = stat_day.get("selected_mean_loss")
        geom_daily = geom_day.get("selected_mean_loss")
        out.append(
            {
                "delivery_date": day,
                "leave_one_contribution_to_overall_difference": contribution,
                "leave_one_difference_stat_minus_geometry": leave_diff,
                "daily_mean_difference_stat_minus_geometry": stat_daily - geom_daily if stat_daily is not None and geom_daily is not None else None,
                "statistical_daily_mean": stat_daily,
                "geometry_daily_mean": geom_daily,
                "statistical_selected_weight": float(stat_day.get("selected_weight") or 0.0),
                "geometry_selected_weight": float(geom_day.get("selected_weight") or 0.0),
            }
        )
    return out


def _calendar_days(start: str, end: str) -> list[str]:
    lo = datetime.strptime(start, "%Y-%m-%d")
    hi = datetime.strptime(end, "%Y-%m-%d")
    days = []
    cur = lo
    while cur <= hi:
        days.append(cur.strftime("%Y-%m-%d"))
        cur = cur + pd.Timedelta(days=1)
    return days


def anchored_nonoverlap_blocks(start: str, end: str, block_days: int) -> list[list[str]]:
    calendar = _calendar_days(start, end)
    anchor = datetime(1970, 1, 1)
    blocks: dict[int, list[str]] = {}
    for day in calendar:
        dt = datetime.strptime(day, "%Y-%m-%d")
        block_id = (dt - anchor).days // int(block_days)
        blocks.setdefault(block_id, []).append(day)
    return [blocks[key] for key in sorted(blocks)]


def block_bootstrap_stat_vs_geometry(
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    side: str,
    coverage: float,
    repetitions: int,
    seed: int,
    block_days: int,
) -> dict[str, Any]:
    stat = daily_method_stats(daily_rows, "statistical", coverage, side)
    geom = daily_method_stats(daily_rows, "geometry", coverage, side)
    calendar_days = sorted(set(stat) | set(geom))
    if not calendar_days:
        return {"status": "unavailable", "reason": "no_calendar_days"}
    stat_mean, geom_mean, mean_diff, overall_ratio = selected_mean_difference_for_days(stat, geom, calendar_days)
    if geom_mean is None or stat_mean is None or mean_diff is None:
        return {"status": "unavailable", "reason": "zero_selected_weight"}
    ratios = {}
    for day in calendar_days:
        stat_daily = (stat.get(day) or {}).get("selected_mean_loss")
        geom_daily = (geom.get(day) or {}).get("selected_mean_loss")
        if stat_daily is not None and geom_daily is not None and abs(geom_daily) > 1e-15:
            ratios[day] = stat_daily / geom_daily
    blocks = anchored_nonoverlap_blocks(min(calendar_days), max(calendar_days), block_days)
    rng = random.Random(int(seed))
    sampled_diffs: list[float] = []
    sampled_ratios: list[float] = []
    for _ in range(int(repetitions)):
        picked: list[str] = []
        for _block in blocks:
            picked.extend(rng.choice(blocks))
        s, g, diff, ratio = selected_mean_difference_for_days(stat, geom, picked)
        if diff is not None:
            sampled_diffs.append(diff)
        if ratio is not None:
            sampled_ratios.append(ratio)
    contributions = leave_one_contributions(stat, geom, calendar_days, mean_diff)
    finite_contributions = [item for item in contributions if item["leave_one_contribution_to_overall_difference"] is not None]
    contribution_ordered = sorted(finite_contributions, key=lambda item: item["leave_one_contribution_to_overall_difference"])
    leave_days = [day for day in calendar_days if day not in {item["delivery_date"] for item in contribution_ordered[:10]}] if len(contribution_ordered) > 10 else []
    leave_stat, leave_geom, leave_diff, leave_ratio = selected_mean_difference_for_days(stat, geom, leave_days) if leave_days else (None, None, None, None)
    annual = {}
    for year in sorted({day[:4] for day in calendar_days}):
        days = [day for day in calendar_days if day.startswith(year)]
        annual_stat, annual_geom, annual_diff, annual_ratio = selected_mean_difference_for_days(stat, geom, days)
        annual[year] = {
            "days": len(days),
            "mean_difference_stat_minus_geometry": annual_diff,
            "statistical_to_geometry_ratio": annual_ratio,
        }
    ci_probs = [0.0125, 0.9875]
    return {
        "status": "available",
        "side": side,
        "coverage": float(coverage),
        "days": len(calendar_days),
        "mean_difference_stat_minus_geometry": mean_diff,
        "statistical_to_geometry_ratio": overall_ratio,
        "daily_ratio_mean": float(sum(ratios.values()) / len(ratios)) if ratios else None,
        "difference_definition": "statistical_selected_loss_sum/statistical_selected_weight minus geometry_selected_loss_sum/geometry_selected_weight over the same calendar days; zero selected-weight days contribute zero numerator and denominator for that method and do not remove the other method's day",
        "leave_one_contribution_definition": "full_difference minus difference_after_removing_that_date; negative contribution is favorable to statistical",
        "bootstrap_block_days": int(block_days),
        "bootstrap_definition": "nonoverlapping calendar blocks anchored 1970-01-01; paired methods share sampled blocks within this comparison",
        "bootstrap_repetitions": int(repetitions),
        "bootstrap_difference_center_97_5_ci": list(np.quantile(sampled_diffs, ci_probs)) if sampled_diffs else None,
        "bootstrap_difference_upper_98_75": float(np.quantile(sampled_diffs, 0.9875)) if sampled_diffs else None,
        "bootstrap_ratio_center_97_5_ci": list(np.quantile(sampled_ratios, ci_probs)) if sampled_ratios else None,
        "top10_favorable_days_for_statistical": [
            item
            for item in contribution_ordered[:10]
        ],
        "top10_adverse_days_for_statistical": [
            item
            for item in sorted(finite_contributions, key=lambda item: item["leave_one_contribution_to_overall_difference"], reverse=True)[:10]
        ],
        "leave_10_most_favorable_out": {
            "days": len(leave_days),
            "mean_difference_stat_minus_geometry": leave_diff,
            "statistical_to_geometry_ratio": leave_ratio,
        },
        "daily_leave_one_contributions": contributions,
        "annual": annual,
    }


def diagnostic_development_support_for_side(methods_summary: Mapping[str, Any], stat_vs_geometry: Mapping[str, Any], side: str) -> dict[str, Any]:
    stat_metrics = ((methods_summary.get("statistical") or {}).get("by_side") or {}).get(side) or {}
    geom_metrics = ((methods_summary.get("geometry") or {}).get("by_side") or {}).get(side) or {}
    annual = stat_vs_geometry.get("annual") or {}
    annual_nonworse = [
        item.get("mean_difference_stat_minus_geometry")
        for item in annual.values()
        if item.get("mean_difference_stat_minus_geometry") is not None
    ]
    checks = {
        "ci_upper_98_75_below_zero": (stat_vs_geometry.get("bootstrap_difference_upper_98_75") is not None and stat_vs_geometry.get("bootstrap_difference_upper_98_75") < 0),
        "annual_nonworse_at_least_3_of_4": sum(float(value) <= 0 for value in annual_nonworse) >= 3 and len(annual_nonworse) >= 4,
        "es95_nonworse": (
            stat_metrics.get("selected_es95_uncapped") is not None
            and geom_metrics.get("selected_es95_uncapped") is not None
            and stat_metrics.get("selected_es95_uncapped") <= geom_metrics.get("selected_es95_uncapped") + 1e-15
        ),
        "leave_10_most_favorable_out_still_improves": (
            (stat_vs_geometry.get("leave_10_most_favorable_out") or {}).get("mean_difference_stat_minus_geometry") is not None
            and (stat_vs_geometry.get("leave_10_most_favorable_out") or {}).get("mean_difference_stat_minus_geometry") < 0
        ),
        "informative_weight_at_least_50pct": (
            stat_metrics.get("informative_weight_fraction") is not None and stat_metrics.get("informative_weight_fraction") >= 0.5
        ),
    }
    return {
        "production": False,
        "support": bool(all(checks.values())),
        "checks": checks,
        "annual_nonworse_count": int(sum(float(value) <= 0 for value in annual_nonworse)),
        "annual_observed_count": int(len(annual_nonworse)),
    }


def evaluate_opportunity(
    rows: pd.DataFrame,
    protocol: Mapping[str, Any],
    *,
    bootstrap_repetitions: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    opportunity = protocol["opportunity"]
    coverages = [float(x) for x in opportunity["curve_coverages"]]
    primary_coverage = float(opportunity["primary_coverage"])
    min_days = int(opportunity["minimum_stratum_days"])
    risk_edges = [float(x) for x in opportunity["risk_display_edges"]]
    uncertainty = protocol.get("uncertainty") or {}
    repetitions = int(bootstrap_repetitions or uncertainty.get("replicates") or 2000)
    seed = int(uncertainty.get("seed") or 20260921)
    block_days = int(uncertainty.get("calendar_block_days") or 7)

    work = rows.copy()
    for method in CANDIDATE_FILES:
        work[f"{method}_score"] = pd.to_numeric(work[f"{method}_expected_loss_normalized"], errors="coerce")
        if work[f"{method}_score"].isna().any():
            raise ValueError(f"missing_score:{method}")
    work["actual_loss_normalized"] = pd.to_numeric(work["actual_loss_normalized"], errors="coerce").astype(float)
    work["row_weight"] = side_day_weights(work)
    methods_summary: dict[str, Any] = {method: {"by_side": {}, "annual": {}, "coverage_curve": {}} for method in CANDIDATE_FILES}
    daily_rows: list[dict[str, Any]] = []
    stratum_rows: list[dict[str, Any]] = []

    for coverage in coverages:
        for method in CANDIDATE_FILES:
            membership, meta = allocate_fractional_coverage(
                work,
                f"{method}_score",
                coverage,
                min_stratum_days=min_days,
            )
            col = f"{method}_member_{coverage:g}"
            work[col] = membership
            for item in meta:
                stratum_rows.append({"method": method, "coverage": coverage, **item})
            for side, part in work.groupby("side", sort=True):
                metrics = membership_metrics(part, membership.loc[part.index])
                sparse_weight = sum(item["total_weight"] for item in meta if item["sparse_uniform"] and f"|{side}|" in item["stratum_key"])
                total_weight = float(part["row_weight"].sum())
                metrics["sparse_uniform_weight_fraction"] = sparse_weight / total_weight if total_weight > 0 else None
                metrics["informative_weight_fraction"] = 1.0 - metrics["sparse_uniform_weight_fraction"] if metrics["sparse_uniform_weight_fraction"] is not None else None
                methods_summary[method]["coverage_curve"].setdefault(side, {})[str(coverage)] = metrics
                if coverage == primary_coverage:
                    methods_summary[method]["by_side"][side] = metrics
                    methods_summary[method]["by_side"][side]["risk_display_bins"] = risk_display_bins(part, f"{method}_score", risk_edges)
                    for year, ypart in part.groupby("year", sort=True):
                        methods_summary[method]["annual"].setdefault(side, {})[str(year)] = membership_metrics(ypart, membership.loc[ypart.index])
            daily_rows.extend(daily_sufficient_stats(work, method, coverage, membership))

    daily = pd.DataFrame(daily_rows)
    stratum = pd.DataFrame(stratum_rows)
    stat_vs_geometry = {
        side: block_bootstrap_stat_vs_geometry(
            daily_rows,
            side=side,
            coverage=primary_coverage,
            repetitions=repetitions,
            seed=seed,
            block_days=block_days,
        )
        for side in sorted(work["side"].unique())
    }
    development_support = {
        side: diagnostic_development_support_for_side(methods_summary, stat_vs_geometry[side], side)
        for side in sorted(work["side"].unique())
    }
    summary = {
        "schema": SCHEMA,
        "research_only": True,
        "qualification": {
            "scope": "retrospective equal-coverage ranking diagnostic",
            "not_policy_replay": True,
            "not_deployable_threshold": True,
            "not_natural_nr_effect": True,
            "not_economic_edge": True,
            "joint_role": "descriptive_saved_prediction_only_not_llm_increment",
        },
        "coverage": {
            "primary": primary_coverage,
            "curve": coverages,
            "row_count": int(len(work)),
            "delivery_days_by_side": {side: int(part["delivery_date"].nunique()) for side, part in work.groupby("side")},
        },
        "methods": methods_summary,
        "statistical_vs_geometry_primary": stat_vs_geometry,
        "diagnostic_development_support": development_support,
        "strata": {
            "count": int(work["stratum_key"].nunique()),
            "sparse_uniform_rows": int(stratum[stratum["sparse_uniform"]].shape[0]) if not stratum.empty else 0,
            "records": stratum.to_dict("records") if not stratum.empty else [],
        },
        "limitations": [
            "uses already-saved v1.1 predictions; no retraining and no new inference",
            "all years are already studied development evidence",
            "coverage fractions are retrospective equal-coverage ranking diagnostics, not executable thresholds",
            "payoff-only labels omit contemporaneous executable credit, so true EV and true win rate are null",
        ],
    }
    return summary, work, daily


def verify_protocol_and_manifest(protocol_path: str | Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol_path = Path(protocol_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    seal_path = protocol_path.with_name("protocol_seal.json")
    manifest_path = protocol_path.with_name("source_manifest.json")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    manifest_hash = digest_file(manifest_path)
    protocol_hash = digest_file(protocol_path)
    if protocol_hash.lower() != str(seal.get("protocol_sha256", "")).lower():
        raise ValueError(f"protocol_hash_mismatch:{protocol_hash}:{seal.get('protocol_sha256')}")
    expected_manifest_hashes = {str(protocol.get("source_manifest_sha256", "")).lower(), str(seal.get("source_manifest_sha256", "")).lower()}
    if manifest_hash.lower() not in expected_manifest_hashes:
        raise ValueError(f"source_manifest_hash_mismatch:{manifest_hash}:{expected_manifest_hashes}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = []
    verified_files = {}
    for file_name, meta in (manifest.get("files") or {}).items():
        path = Path(file_name)
        if not path.exists():
            mismatches.append({"path": file_name, "reason": "missing"})
            continue
        actual = digest_file(path)
        expected = str(meta.get("sha256", "")).lower()
        if actual.lower() != expected:
            mismatches.append({"path": file_name, "expected": expected, "actual": actual})
        verified_files[file_name] = {"sha256": actual, "bytes": path.stat().st_size}
    if mismatches:
        raise ValueError(f"source_manifest_file_mismatch:{mismatches[:3]}")
    return protocol, seal, {"source_manifest_sha256": manifest_hash, "files": verified_files, "scope": manifest.get("scope")}


def verify_optional_preresult_amendment(protocol_path: str | Path) -> dict[str, Any] | None:
    protocol_path = Path(protocol_path)
    amendment_path = protocol_path.with_name("preresult_amendment_01.json")
    if not amendment_path.exists():
        return None
    hash_path = protocol_path.with_name("preresult_amendment_01.sha256")
    if not hash_path.exists():
        hash_path = protocol_path.with_name("preresult_amendment_01.json.sha256")
    if not hash_path.exists():
        raise ValueError("preresult_amendment_hash_file_missing")
    expected = hash_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = digest_file(amendment_path).lower()
    if actual != expected:
        raise ValueError(f"preresult_amendment_hash_mismatch:{actual}:{expected}")
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    return {
        "path": str(amendment_path),
        "sha256": actual,
        "hash_file": str(hash_path),
        "schema": amendment.get("schema"),
        "new_results_observed": amendment.get("new_results_observed"),
        "changes": amendment.get("changes"),
    }


def _read_csv_required(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=lambda col: col in set(columns))
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"missing_columns:{path}:{missing}")
    return df


def load_prediction_frames(research_v11: str | Path, years: Sequence[int]) -> dict[str, pd.DataFrame]:
    root = Path(research_v11)
    folder = root / "step30" / "models_annual_calibrated_20260921" / "rolling_candidate_predictions"
    frames: dict[str, pd.DataFrame] = {}
    year_set = {int(year) for year in years}
    for method, filename in CANDIDATE_FILES.items():
        path = folder / filename
        df = _read_csv_required(path, PREDICTION_COLUMNS)
        df["side"] = df["side"].map(canonical_side)
        df["year"] = df["delivery_date"].str[:4].astype(int)
        df = df[df["year"].isin(year_set)].copy()
        df = df[np.isclose(pd.to_numeric(df["target_width"], errors="coerce"), 2000.0, atol=1e-9, rtol=0)].copy()
        if df["row_id"].duplicated().any():
            raise ValueError(f"duplicate_prediction_row_id:{method}")
        frames[method] = df
    base = frames["geometry"].set_index("row_id").sort_index()
    for method, frame in frames.items():
        check = frame.set_index("row_id").sort_index()
        pd.testing.assert_index_equal(base.index, check.index, obj=f"{method}_row_ids")
        for col in [
            "observation_id",
            "as_of_ms",
            "entry_ms",
            "expiry_ms",
            "delivery_date",
            "side",
            "target_width",
            "actual_width",
            "actual_loss_normalized",
            "actual_payout_btc",
            "protection_breached",
        ]:
            if col in {"target_width", "actual_width", "actual_loss_normalized", "actual_payout_btc"}:
                if not np.allclose(pd.to_numeric(base[col]), pd.to_numeric(check[col]), atol=1e-10, rtol=0):
                    raise ValueError(f"prediction_identity_mismatch:{method}:{col}")
            else:
                left = base[col].map(str)
                right = check[col].map(str)
                if not left.equals(right):
                    raise ValueError(f"prediction_identity_mismatch:{method}:{col}")
    return frames


def load_model_input_rows(research_v11: str | Path, years: Sequence[int]) -> pd.DataFrame:
    root = Path(research_v11)
    frames = []
    for year in years:
        path = root / "step30" / "model_input" / f"model_rows-{int(year)}.csv"
        for chunk in pd.read_csv(path, usecols=lambda col: col in set(MODEL_INPUT_COLUMNS), chunksize=50000):
            missing = [col for col in MODEL_INPUT_COLUMNS if col not in chunk.columns]
            if missing:
                raise ValueError(f"missing_columns:{path}:{missing}")
            chunk = chunk[np.isclose(pd.to_numeric(chunk["target_width"], errors="coerce"), 2000.0, atol=1e-9, rtol=0)].copy()
            if not chunk.empty:
                frames.append(chunk)
    if not frames:
        raise ValueError("no_model_input_rows")
    rows = pd.concat(frames, ignore_index=True)
    rows["side"] = rows["side"].map(canonical_side)
    if rows["row_id"].duplicated().any():
        raise ValueError("duplicate_model_input_row_id")
    return rows


def merge_predictions_with_inputs(predictions: Mapping[str, pd.DataFrame], inputs: pd.DataFrame) -> pd.DataFrame:
    base = predictions["geometry"].copy()
    for method, frame in predictions.items():
        base[f"{method}_expected_loss_normalized"] = base["row_id"].map(frame.set_index("row_id")["expected_loss_normalized"])
        base[f"{method}_probability_positive"] = base["row_id"].map(frame.set_index("row_id")["probability_positive"])
        base[f"{method}_breach_probability"] = base["row_id"].map(frame.set_index("row_id")["breach_probability"])
    merged = base.merge(inputs, on="row_id", how="left", suffixes=("_pred", "_input"), validate="one_to_one")
    if merged["observation_id_input"].isna().any():
        missing = merged.loc[merged["observation_id_input"].isna(), "row_id"].head(5).tolist()
        raise ValueError(f"prediction_model_input_missing:{missing}")
    string_cols = ["observation_id", "delivery_date"]
    for col in string_cols:
        left = merged[f"{col}_pred"].map(str)
        right = merged[f"{col}_input"].map(str)
        if not left.equals(right):
            raise ValueError(f"prediction_model_input_identity_mismatch:{col}")
    if not merged["side_pred"].map(canonical_side).equals(merged["side_input"].map(canonical_side)):
        raise ValueError("prediction_model_input_identity_mismatch:side")
    for col in ["as_of_ms", "entry_ms", "expiry_ms", "target_width", "actual_width"]:
        if not np.allclose(pd.to_numeric(merged[f"{col}_pred"]), pd.to_numeric(merged[f"{col}_input"]), atol=1e-9, rtol=0):
            raise ValueError(f"prediction_model_input_identity_mismatch:{col}")
    if not np.allclose(pd.to_numeric(merged["actual_loss_normalized"]), pd.to_numeric(merged["loss_normalized"]), atol=1e-10, rtol=0):
        raise ValueError("prediction_model_input_label_mismatch:loss_normalized")
    if not np.allclose(pd.to_numeric(merged["actual_payout_btc"]), pd.to_numeric(merged["payout_btc"]), atol=1e-12, rtol=0):
        raise ValueError("prediction_model_input_label_mismatch:payout_btc")
    pred_prot = merged["protection_breached"].map(bool_or_none)
    input_prot = merged["protection_leg_breached"].map(bool_or_none)
    if not pred_prot.equals(input_prot):
        raise ValueError("prediction_model_input_label_mismatch:protection_breached")
    out = pd.DataFrame(
        {
            "schema": ROW_SCHEMA,
            "row_id": merged["row_id"],
            "observation_id": merged["observation_id_pred"],
            "as_of_ms": pd.to_numeric(merged["as_of_ms_pred"], errors="coerce"),
            "entry_ms": pd.to_numeric(merged["entry_ms_pred"], errors="coerce"),
            "expiry_ms": pd.to_numeric(merged["expiry_ms_pred"], errors="coerce"),
            "delivery_date": merged["delivery_date_pred"],
            "side": merged["side_pred"].map(canonical_side),
            "target_width": pd.to_numeric(merged["target_width_pred"], errors="coerce"),
            "actual_width": pd.to_numeric(merged["actual_width_pred"], errors="coerce"),
            "entry_price": pd.to_numeric(merged["entry_price"], errors="coerce"),
            "dte_hours": pd.to_numeric(merged["dte_hours"], errors="coerce"),
            "short_distance_fraction": pd.to_numeric(merged["short_distance_fraction"], errors="coerce"),
            "width_fraction": pd.to_numeric(merged["width_fraction"], errors="coerce"),
            "vol_240": pd.to_numeric(merged["vol_240"], errors="coerce"),
            "actual_loss_normalized": pd.to_numeric(merged["actual_loss_normalized"], errors="coerce"),
            "actual_payout_btc": pd.to_numeric(merged["actual_payout_btc"], errors="coerce"),
            "short_leg_breached_bool": merged["short_leg_breached"].map(bool_or_none),
            "protection_breached_bool": input_prot,
        }
    )
    for method in CANDIDATE_FILES:
        out[f"{method}_expected_loss_normalized"] = pd.to_numeric(merged[f"{method}_expected_loss_normalized"], errors="coerce")
        out[f"{method}_probability_positive"] = pd.to_numeric(merged[f"{method}_probability_positive"], errors="coerce")
        out[f"{method}_breach_probability"] = pd.to_numeric(merged[f"{method}_breach_probability"], errors="coerce")
    if out[["dte_hours", "short_distance_fraction", "actual_loss_normalized"]].isna().any().any():
        raise ValueError("merged_required_numeric_missing")
    return out


def write_summary_md(path: Path, summary: Mapping[str, Any]) -> None:
    lines = [
        "# Astra Entry Quality v1.3",
        "",
        "Research-only retrospective equal-coverage ranking diagnostic. It reuses saved v1.1 predictions and does not train, infer, deploy, or claim economic edge.",
        "",
        f"- Rows: {summary['coverage']['row_count']}",
        f"- Primary coverage: {summary['coverage']['primary']}",
        f"- Qualification: {summary['qualification']['scope']}",
        "",
        "## Primary Statistical vs Geometry",
    ]
    for side, item in (summary.get("statistical_vs_geometry_primary") or {}).items():
        lines.append(
            f"- {side}: mean diff={item.get('mean_difference_stat_minus_geometry')}, upper98.75={item.get('bootstrap_difference_upper_98_75')}, days={item.get('days')}"
        )
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- Retrospective equal-coverage ranking diagnostic only; no executable threshold.",
            "- Joint scores are saved model predictions, not an LLM-effect measurement.",
            "- Net EV and win rate are null without synchronous historical credit.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_opportunity(
    *,
    research_v11: str | Path,
    protocol_path: str | Path,
    output_dir: str | Path,
    bootstrap_repetitions: int | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    try:
        protocol, seal, source_verification = verify_protocol_and_manifest(protocol_path)
        preresult_amendment = verify_optional_preresult_amendment(protocol_path)
        years = [int(year) for year in protocol["identity"]["years"]]
        fit_source_years = [int(year) for year in protocol["identity"].get("fit_source_years", years) if int(year) <= 2025]
        predictions = load_prediction_frames(research_v11, years)
        inputs = load_model_input_rows(research_v11, fit_source_years)
        merged = merge_predictions_with_inputs(predictions, inputs)
        fit_medians = compute_fit_vol_medians(inputs, years)
        with_strata = add_opportunity_strata(merged, protocol, fit_medians)
        summary, rows, daily = evaluate_opportunity(with_strata, protocol, bootstrap_repetitions=bootstrap_repetitions)
        summary["protocol_sha256"] = digest_file(protocol_path)
        summary["protocol_seal"] = seal
        summary["source_verification"] = source_verification
        summary["preresult_amendment"] = preresult_amendment
        summary["fit_vol_medians"] = {f"{year}:{side}": value for (year, side), value in fit_medians.items()}
        row_cols = [
            "schema",
            "row_id",
            "observation_id",
            "delivery_date",
            "year",
            "side",
            "actual_width",
            "dte_hours",
            "short_distance_fraction",
            "vol_240",
            "stratum_key",
            "row_weight",
            "actual_loss_normalized",
            "protection_breached_bool",
        ]
        for method in CANDIDATE_FILES:
            row_cols.extend([f"{method}_score", f"{method}_member_{float(protocol['opportunity']['primary_coverage']):g}"])
        rows[row_cols].to_csv(output / "row_scores_main_membership.csv", index=False)
        daily.to_csv(output / "daily_sufficient_stats.csv", index=False)
        (output / "summary.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        write_summary_md(output / "summary.md", summary)
        return summary
    except Exception as exc:
        failure = {
            "schema": "astra_entry_quality_failure@1.0.0",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            (output / "failure.json").write_text(json.dumps(json_safe(failure), ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sealed Astra v1.3 opportunity-quality diagnostics.")
    parser.add_argument("--research-v11", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repetitions", type=int, default=None)
    args = parser.parse_args(argv)
    summary = run_opportunity(
        research_v11=args.research_v11,
        protocol_path=args.protocol,
        output_dir=args.output,
        bootstrap_repetitions=args.bootstrap_repetitions,
    )
    print(json.dumps(json_safe({"schema": summary["schema"], "coverage": summary["coverage"], "qualification": summary["qualification"]}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
