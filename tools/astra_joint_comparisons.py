"""Supplementary comparisons for sealed Astra joint research.

This stage does not retrain, reselect, or edit the locked-test report. It reads
the sealed feature-group artifacts plus already created 2023 outcomes, then
writes a separate supplementary JSON/Markdown report.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from astra_joint_contract import PROTOCOL
from astra_joint_sources import digest, save
import astra_joint_experiment as experiment
import astra_joint_inference as inference
import astra_joint_model as model
import astra_joint_pipeline as pipeline


SUPPLEMENTARY_SCHEMA = "astra_joint_supplementary_comparisons@1.0.1"
PRIMARY_FEATURE_GROUPS = ("statistical", "mechanism", "joint")
LOCKED_TEST_YEARS = tuple(PROTOCOL["test_years"])
DEFAULT_OUTPUT_JSON = "supplementary_comparisons.json"
DEFAULT_OUTPUT_MD = "supplementary_comparisons.md"
UNACCEPTED_OUTPUT_JSON = "supplementary_comparisons.unaccepted-v1.0.0.json"
UNACCEPTED_OUTPUT_MD = "supplementary_comparisons.unaccepted-v1.0.0.md"
EPSILON = 1e-12


class SupplementaryComparisonError(RuntimeError):
    """Raised when a supplementary stage would violate the sealed workflow."""


def run_supplementary_comparisons(
    root: Path,
    *,
    credit_scenarios: Sequence[float] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = Path(root)
    models_dir = root / "models"
    out_json = models_dir / DEFAULT_OUTPUT_JSON
    out_md = models_dir / DEFAULT_OUTPUT_MD
    if not overwrite and (out_json.exists() or out_md.exists()):
        raise SupplementaryComparisonError("supplementary comparisons already exist; use overwrite only for an explicit rerun")
    if overwrite:
        preserve_unaccepted_report(models_dir)

    selection, selection_seal, selection_validation = read_valid_selection(root)
    locked_report_path = models_dir / "locked_test_report.json"
    if not locked_report_path.exists():
        raise SupplementaryComparisonError("locked_test_report.json is required before supplementary comparisons")
    ensure_locked_outcomes_exist(root)

    rows = pipeline.joined_rows(root, LOCKED_TEST_YEARS, primary_only=True)
    credit_scenarios = [float(value) for value in (credit_scenarios or PROTOCOL["net_credit_scenarios"])]
    pairs = paired_side_rows(rows)

    prediction_cache: dict[str, dict[str, dict[str, Any]]] = {}
    corrected_primary = corrected_primary_comparisons(selection, rows, prediction_cache=prediction_cache)
    feature_group_results: dict[str, Any] = {}
    secondary_p_values: dict[str, float] = {}
    for group in PRIMARY_FEATURE_GROUPS:
        pick = (selection.get("best_artifacts") or {}).get(group) or {}
        artifact = pick.get("selected_artifact")
        if not artifact:
            feature_group_results[group] = {
                "status": "unavailable",
                "reason": "no_selected_artifact_for_feature_group",
                "selected": pick.get("selected"),
            }
            continue
        result = evaluate_model_side_selection(group, artifact, pairs, credit_scenarios, prediction_cache=prediction_cache)
        feature_group_results[group] = result
        collect_choice_p_values(secondary_p_values, group, "all_pairs", result.get("comparisons", {}))
        collect_choice_p_values(
            secondary_p_values,
            group,
            "same_actual_width",
            (result.get("same_actual_width_subset") or {}).get("comparisons", {}),
        )
    apply_holm(feature_group_results, model.holm_adjust(secondary_p_values) if secondary_p_values else {})

    payload = {
        "schema": SUPPLEMENTARY_SCHEMA,
        "created_at_utc": now_utc(),
        "selection_result_sha256": digest(models_dir / "selection_result.json"),
        "selection_seal_sha256": digest(models_dir / "selection_seal.json"),
        "locked_test_report_sha256": digest(locked_report_path),
        "selection_validation": selection_validation,
        "protocol": experiment.protocol_subset(),
        "scope": {
            "stage": "supplementary_locked_test_comparisons",
            "locked_test_years": list(LOCKED_TEST_YEARS),
            "primary_width": PROTOCOL["primary_width"],
            "credit_scenarios": credit_scenarios,
            "model_selection_frozen": True,
            "no_refit_no_reselection": True,
            "quote_status": "no_historical_quotes_used; net-credit results are scenario assumptions",
            "selection_sealed_at_utc": selection_seal.get("sealed_at_utc"),
            "math_report_version": "1.0.1",
            "math_report_correction": "primary_holm_family_separated; equal_put_call_win_rate_uses_side_indicators; breakeven_is_not_win; no_retraining",
        },
        "coverage": coverage_summary(rows, pairs),
        "corrected_primary_comparisons": corrected_primary,
        "feature_group_results": feature_group_results,
        "timing_transitions": episode_transition_comparisons(rows),
        "notes": [
            "Corrected primary comparisons are a math-report repair only. They reuse the sealed artifacts and rows; the original locked_test_report is preserved.",
            "Model side selection uses the lower predicted BTC payout. Without synchronized historical quotes, this is payout-risk evidence, not a complete trade-worthiness or net-edge proof.",
            "Primary summaries are delivery-date equal weighted. Raw row-weighted summaries are retained for diagnostics.",
            "Corrected primary MAE comparisons use an independent Holm family containing only joint-vs-statistical and joint-vs-mechanism.",
            "Supplementary paired comparisons are secondary and multiplicity-adjusted in a separate family when p-values are available.",
            "Net-credit win rate treats net == 0 as breakeven, not a win; equal Put/Call risk averages each side's own win indicator.",
            "Equal Put/Call risk tail loss uses the two single-side outcomes at 0.5 weight each, not the sign or tail of the averaged net value.",
        ],
    }
    save(out_json, payload, immutable=not overwrite)
    write_markdown(out_md, render_markdown(payload), overwrite=overwrite)
    return payload


def repair_existing_supplementary_report(root: Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Repair only report math from stored sample records without new inference."""

    root = Path(root)
    models_dir = root / "models"
    out_json = models_dir / DEFAULT_OUTPUT_JSON
    out_md = models_dir / DEFAULT_OUTPUT_MD
    if not out_json.exists():
        raise SupplementaryComparisonError("supplementary_comparisons.json is required for sample-record repair")
    if not overwrite:
        raise SupplementaryComparisonError("repairing an existing report requires overwrite and preserves an unaccepted copy first")
    preserve_unaccepted_report(models_dir)
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    credit_scenarios = [
        float(value)
        for value in (((payload.get("scope") or {}).get("credit_scenarios")) or PROTOCOL["net_credit_scenarios"])
    ]
    feature_group_results = payload.get("feature_group_results") or {}
    secondary_p_values: dict[str, float] = {}
    repaired_groups: dict[str, Any] = {}
    for group, result in feature_group_results.items():
        if not isinstance(result, dict) or result.get("status") != "available":
            repaired_groups[group] = result
            continue
        records = [normalize_choice_record(record, credit_scenarios) for record in (result.get("sample_records") or [])]
        if not records:
            repaired_groups[group] = result
            continue
        repaired = rebuild_feature_group_result(result, records, credit_scenarios)
        repaired_groups[group] = repaired
        collect_choice_p_values(secondary_p_values, group, "all_pairs", repaired.get("comparisons", {}))
        collect_choice_p_values(
            secondary_p_values,
            group,
            "same_actual_width",
            (repaired.get("same_actual_width_subset") or {}).get("comparisons", {}),
        )
    apply_holm(repaired_groups, model.holm_adjust(secondary_p_values) if secondary_p_values else {})
    payload["schema"] = SUPPLEMENTARY_SCHEMA
    payload["created_at_utc"] = now_utc()
    payload["feature_group_results"] = repaired_groups
    repair_corrected_primary_holm(payload.get("corrected_primary_comparisons") or {})
    payload.setdefault("scope", {})["math_report_version"] = "1.0.1"
    payload.setdefault("scope", {})["math_report_correction"] = (
        "recomputed_from_existing_sample_records_no_retraining_no_prediction; "
        "primary_holm_family_separated; equal_put_call_win_rate_uses_side_indicators; "
        "breakeven_is_not_win"
    )
    payload["repair"] = {
        "schema": "astra_joint_supplementary_math_repair@1.0.1",
        "created_at_utc": payload["created_at_utc"],
        "method": "existing_sample_records_only",
        "source_unaccepted_json": UNACCEPTED_OUTPUT_JSON if (models_dir / UNACCEPTED_OUTPUT_JSON).exists() else None,
        "source_unaccepted_md": UNACCEPTED_OUTPUT_MD if (models_dir / UNACCEPTED_OUTPUT_MD).exists() else None,
        "no_retraining": True,
        "no_prediction_rerun": True,
    }
    notes = list(payload.get("notes") or [])
    notes.extend(
        [
            "This version repairs the supplementary math report from stored sample_records only; it does not retrain, reselect, or rerun model inference.",
            "Primary Holm adjustment is kept separate from secondary model-choice comparisons.",
            "Net-credit breakeven is counted separately from wins; equal Put/Call risk uses the average of each side's own win indicator.",
            "Equal Put/Call risk tail loss is repaired from the single-side component distribution rather than the averaged net value.",
        ]
    )
    payload["notes"] = dedupe_preserve_order(notes)
    save(out_json, payload, immutable=False)
    write_markdown(out_md, render_markdown(payload), overwrite=True)
    return payload


def preserve_unaccepted_report(models_dir: Path) -> None:
    models_dir = Path(models_dir)
    for source_name, target_name in ((DEFAULT_OUTPUT_JSON, UNACCEPTED_OUTPUT_JSON), (DEFAULT_OUTPUT_MD, UNACCEPTED_OUTPUT_MD)):
        source = models_dir / source_name
        target = models_dir / target_name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def read_valid_selection(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    models_dir = Path(root) / "models"
    result_path = models_dir / "selection_result.json"
    seal_path = models_dir / "selection_seal.json"
    if not result_path.exists() or not seal_path.exists():
        raise SupplementaryComparisonError("selection_result.json and selection_seal.json are required")
    try:
        return experiment.load_and_validate_selection(root, result_path, seal_path)
    except Exception as exc:
        raise SupplementaryComparisonError(f"selection validation failed before supplementary comparisons: {exc}") from exc


def ensure_locked_outcomes_exist(root: Path) -> None:
    missing = [year for year in LOCKED_TEST_YEARS if not (Path(root) / "outcomes" / f"outcomes-{year}.csv").exists()]
    if missing:
        raise SupplementaryComparisonError(f"locked-test outcomes are missing and will not be generated here: {missing}")


def corrected_primary_comparisons(
    selection: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    prediction_cache: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    best = selection.get("best_artifacts") or {}
    result: dict[str, Any] = {
        "role": "math_report_correction_not_retraining",
        "metric": "expected_loss_mae_by_delivery_day",
        "multiplicity_family": "primary_mae_only",
        "comparisons": {},
    }
    p_values: dict[str, float] = {}
    for name, left, right in (
        ("joint_minus_statistical", "joint", "statistical"),
        ("joint_minus_mechanism", "joint", "mechanism"),
    ):
        left_artifact = (best.get(left) or {}).get("selected_artifact")
        right_artifact = (best.get(right) or {}).get("selected_artifact")
        if not left_artifact or not right_artifact:
            result["comparisons"][name] = {"status": "unavailable", "left_feature_group": left, "right_feature_group": right}
            continue
        left_errors = delivery_day_abs_errors(left_artifact, rows, prediction_cache=prediction_cache)
        right_errors = delivery_day_abs_errors(right_artifact, rows, prediction_cache=prediction_cache)
        day = model.bootstrap_metric_difference(left_errors, right_errors, block_days=1)
        week = model.bootstrap_metric_difference(left_errors, right_errors, block_days=7)
        result["comparisons"][name] = {
            "status": "available",
            "left_feature_group": left,
            "right_feature_group": right,
            "metric_direction": "negative means the left feature group has lower MAE",
            "day_block": day,
            "seven_day_block": week,
        }
        if week.get("p_value_two_sided_centered") is not None:
            p_values[f"corrected_primary_comparisons.{name}.seven_day_block"] = float(week["p_value_two_sided_centered"])
    for key, value in model.holm_adjust(p_values).items():
        name = key.split(".")[1]
        result["comparisons"][name]["seven_day_block"]["holm_adjusted_p"] = value
    return result


def repair_corrected_primary_holm(corrected_primary: dict[str, Any]) -> None:
    p_values: dict[str, float] = {}
    for name, item in (corrected_primary.get("comparisons") or {}).items():
        p_value = ((item.get("seven_day_block") or {}).get("p_value_two_sided_centered"))
        if p_value is not None:
            p_values[f"corrected_primary_comparisons.{name}.seven_day_block"] = float(p_value)
    for key, value in model.holm_adjust(p_values).items():
        name = key.split(".")[1]
        item = (corrected_primary.get("comparisons") or {}).get(name)
        if isinstance(item, dict):
            item.setdefault("seven_day_block", {})["holm_adjusted_p"] = value
    corrected_primary["multiplicity_family"] = "primary_mae_only"


def delivery_day_abs_errors(
    artifact: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    prediction_cache: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, float]:
    eval_rows = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    predictions = cached_predictions(artifact, eval_rows, prediction_cache)
    by_day: dict[str, list[float]] = defaultdict(list)
    for row in eval_rows:
        prediction = predictions.get(row_cache_id(row)) or {}
        predicted = finite_float(prediction.get("expected_loss_normalized"))
        target = model.target_loss(row)
        if predicted is not None and target is not None:
            by_day[str(row.get("delivery_date"))].append(abs(predicted - float(target)))
    return day_means(by_day)


def evaluate_model_side_selection(
    feature_group: str,
    artifact: dict[str, Any],
    pairs: Sequence[dict[str, dict[str, Any]]],
    credit_scenarios: Sequence[float],
    *,
    prediction_cache: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    flat_rows = [row for pair in pairs for row in (pair["put_credit"], pair["call_credit"])]
    predictions = cached_predictions(artifact, flat_rows, prediction_cache)
    records = build_choice_records(pairs, predictions, credit_scenarios)
    result = summarize_model_records(records, credit_scenarios)
    result.update(
        {
            "status": "available" if records else "unavailable",
            "feature_group": feature_group,
            "artifact_hash": artifact.get("artifact_hash"),
            "selected_basis": "lower_predicted_btc_payout",
            "baseline_note": "Fixed Put, Fixed Call, and equal Put/Call risk are paired on the same observation identity. Equal risk is the half-weighted average of both sides, not a double-sell trade.",
        }
    )
    result["same_actual_width_subset"] = summarize_model_records(
        [record for record in records if record["same_actual_width"]],
        credit_scenarios,
    )
    result["sample_records"] = records
    return result


def cached_predictions(
    artifact: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    cache: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, Any]]:
    if cache is None:
        return {row_cache_id(row): prediction for row, prediction in zip(rows, inference.predict_rows(artifact, rows))}
    artifact_key = artifact_cache_id(artifact)
    stored = cache.setdefault(artifact_key, {})
    missing = [row for row in rows if row_cache_id(row) not in stored]
    if missing:
        for row, prediction in zip(missing, inference.predict_rows(artifact, missing)):
            stored[row_cache_id(row)] = prediction
    return {row_cache_id(row): stored[row_cache_id(row)] for row in rows if row_cache_id(row) in stored}


def artifact_cache_id(artifact: dict[str, Any]) -> str:
    direct = artifact.get("artifact_hash")
    return str(direct) if direct else model.stable_hash(artifact)


def row_cache_id(row: dict[str, Any]) -> str:
    direct = row.get("row_id")
    return str(direct) if direct not in (None, "") else model.stable_hash(row)


def build_choice_records(
    pairs: Sequence[dict[str, dict[str, Any]]],
    predictions: dict[str, dict[str, Any]],
    credit_scenarios: Sequence[float],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for pair in pairs:
        put = pair["put_credit"]
        call = pair["call_credit"]
        put_values = row_values(put, predictions.get(str(put.get("row_id"))), credit_scenarios)
        call_values = row_values(call, predictions.get(str(call.get("row_id"))), credit_scenarios)
        if not put_values or not call_values:
            continue
        put_score = float(put_values["predicted_btc_payout"])
        call_score = float(call_values["predicted_btc_payout"])
        tie = abs(put_score - call_score) <= 1e-15
        if tie:
            selected_side = "tie_equal_put_call"
            choice = averaged_values(put_values, call_values, credit_scenarios)
            other_side = None
        elif put_score < call_score:
            selected_side, choice, other_side = "put_credit", put_values, "call_credit"
        else:
            selected_side, choice, other_side = "call_credit", call_values, "put_credit"
        records.append(
            {
                "pair_key": list(row_pair_key(put)),
                "delivery_date": str(put.get("delivery_date") or call.get("delivery_date")),
                "event_family": put.get("event_family"),
                "episode_id": put.get("episode_id"),
                "observation_kind": put.get("observation_kind"),
                "as_of_ms": put.get("as_of_ms"),
                "entry_ms": put.get("entry_ms"),
                "expiry_ms": put.get("expiry_ms"),
                "same_actual_width": same_actual_width_pair(pair),
                "selected_side": selected_side,
                "other_side": other_side,
                "selection_tie": tie,
                "model_choice": choice,
                "baselines": {
                    "fixed_put": put_values,
                    "fixed_call": call_values,
                    "equal_put_call_risk": averaged_values(put_values, call_values, credit_scenarios),
                },
            }
        )
    return records


def row_values(row: dict[str, Any], prediction: dict[str, Any] | None, credit_scenarios: Sequence[float]) -> dict[str, Any] | None:
    side = model.canonical_side(row.get("side"))
    width_btc = width_value_btc(row)
    payout_btc = actual_payout_btc(row)
    predicted_norm = finite_float((prediction or {}).get("expected_loss_normalized"))
    if side is None or width_btc is None or payout_btc is None or predicted_norm is None:
        return None
    net_btc_by_credit = {f"{float(credit):.2f}": float(credit) * width_btc - payout_btc for credit in credit_scenarios}
    return {
        "side": side,
        "row_id": row.get("row_id"),
        "actual_width": finite_float(row.get("actual_width")),
        "entry_price": finite_float(row.get("entry_price")),
        "short_strike": finite_float(row.get("short_strike")),
        "long_strike": finite_float(row.get("long_strike")),
        "dte_hours": finite_float(row.get("dte_hours")),
        "width_btc": width_btc,
        "predicted_loss_normalized": predicted_norm,
        "predicted_btc_payout": predicted_norm * width_btc,
        "actual_loss_normalized": finite_float(row.get("loss_normalized")) or 0.0,
        "actual_payout_btc": payout_btc,
        "net_btc_by_credit": net_btc_by_credit,
        "net_win_by_credit": {key: 1.0 if value > EPSILON else 0.0 for key, value in net_btc_by_credit.items()},
        "net_breakeven_by_credit": {key: 1.0 if abs(value) <= EPSILON else 0.0 for key, value in net_btc_by_credit.items()},
    }


def averaged_values(left: dict[str, Any], right: dict[str, Any], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    credit_keys = [f"{float(credit):.2f}" for credit in credit_scenarios]
    return {
        "side": "equal_put_call_risk",
        "row_id": None,
        "actual_width": None,
        "entry_price": mean_or_none([left.get("entry_price"), right.get("entry_price")]),
        "short_strike": None,
        "long_strike": None,
        "dte_hours": mean_or_none([left.get("dte_hours"), right.get("dte_hours")]),
        "width_btc": mean_or_none([left.get("width_btc"), right.get("width_btc")]),
        "predicted_loss_normalized": mean_or_none([left.get("predicted_loss_normalized"), right.get("predicted_loss_normalized")]),
        "predicted_btc_payout": mean_or_none([left.get("predicted_btc_payout"), right.get("predicted_btc_payout")]),
        "actual_loss_normalized": mean_or_none([left.get("actual_loss_normalized"), right.get("actual_loss_normalized")]),
        "actual_payout_btc": mean_or_none([left.get("actual_payout_btc"), right.get("actual_payout_btc")]),
        "net_btc_by_credit": {
            credit_key: mean_or_none(
                [
                    (left.get("net_btc_by_credit") or {}).get(credit_key),
                    (right.get("net_btc_by_credit") or {}).get(credit_key),
                ]
            )
            for credit_key in credit_keys
        },
        "net_win_by_credit": {
            credit_key: mean_or_none(
                [
                    net_win_from_values(left, credit_key),
                    net_win_from_values(right, credit_key),
                ]
            )
            for credit_key in credit_keys
        },
        "net_breakeven_by_credit": {
            credit_key: mean_or_none(
                [
                    net_breakeven_from_values(left, credit_key),
                    net_breakeven_from_values(right, credit_key),
                ]
            )
            for credit_key in credit_keys
        },
    }


def summarize_model_records(records: Sequence[dict[str, Any]], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    baselines = ("model_choice", "fixed_put", "fixed_call", "equal_put_call_risk")
    payload: dict[str, Any] = {
        "records": len(records),
        "delivery_dates": independent_dates(records),
        "coverage": {
            "same_actual_width_pairs": sum(1 for record in records if record.get("same_actual_width")),
            "different_actual_width_pairs": sum(1 for record in records if not record.get("same_actual_width")),
        },
        "selected_side_counts": count_by(records, lambda record: record.get("selected_side") or "unknown"),
        "standalone": {},
        "comparisons": {},
    }
    for name in baselines:
        payload["standalone"][name] = {
            "actual_payout_btc": value_summary(records, [value_at(record, name, "actual_payout_btc") for record in records], win_threshold=0.0, win_direction="le"),
            "by_credit_scenario": {
                f"{float(credit):.2f}": net_scenario_summary(records, name, credit)
                for credit in credit_scenarios
            },
        }
    for baseline in ("fixed_put", "fixed_call", "equal_put_call_risk"):
        payload["comparisons"][f"model_vs_{baseline}"] = paired_comparison(records, "model_choice", baseline, credit_scenarios)
    return payload


def paired_comparison(records: Sequence[dict[str, Any]], left: str, right: str, credit_scenarios: Sequence[float]) -> dict[str, Any]:
    payout_saving: dict[str, list[float]] = defaultdict(list)
    for record in records:
        left_value = value_at(record, left, "actual_payout_btc")
        right_value = value_at(record, right, "actual_payout_btc")
        if left_value is not None and right_value is not None:
            payout_saving[str(record["delivery_date"])].append(float(right_value) - float(left_value))
    result: dict[str, Any] = {
        "status": "available" if payout_saving else "unavailable",
        "comparison_role": "secondary_supplementary",
        "metric_direction": "positive means model choice saved BTC payout or produced higher scenario net result than baseline",
        "paired_records": sum(len(values) for values in payout_saving.values()),
        "paired_delivery_dates": len(payout_saving),
        "actual_payout_btc_saving": bootstrap_day_and_week(day_means(payout_saving)),
        "by_credit_scenario": {},
    }
    for credit in credit_scenarios:
        differences: dict[str, list[float]] = defaultdict(list)
        for record in records:
            left_value = net_value_at(record, left, credit)
            right_value = net_value_at(record, right, credit)
            if left_value is not None and right_value is not None:
                differences[str(record["delivery_date"])].append(float(left_value) - float(right_value))
        result["by_credit_scenario"][f"{float(credit):.2f}"] = bootstrap_day_and_week(day_means(differences))
    return result


def bootstrap_day_and_week(left_by_day: dict[str, float]) -> dict[str, Any]:
    zero_by_day = {day: 0.0 for day in left_by_day}
    return {
        "day_block": model.bootstrap_metric_difference(left_by_day, zero_by_day, block_days=1),
        "seven_day_block": model.bootstrap_metric_difference(left_by_day, zero_by_day, block_days=7),
    }


def collect_choice_p_values(p_values: dict[str, float], group: str, subset: str, comparisons: dict[str, Any]) -> None:
    for name, item in comparisons.items():
        p_value = (((item.get("actual_payout_btc_saving") or {}).get("seven_day_block") or {}).get("p_value_two_sided_centered"))
        if p_value is not None:
            p_values[holm_path(group, subset, name, "actual_payout_btc_saving")] = float(p_value)
        for credit_key, credit_item in (item.get("by_credit_scenario") or {}).items():
            p_value = (((credit_item.get("seven_day_block") or {}).get("p_value_two_sided_centered")))
            if p_value is not None:
                p_values[holm_path(group, subset, name, "net_credit", credit_key)] = float(p_value)


def holm_path(group: str, subset: str, comparison: str, metric: str, credit_key: str | None = None) -> str:
    return json.dumps(
        {
            "family": "secondary_model_choice",
            "group": group,
            "subset": subset,
            "comparison": comparison,
            "metric": metric,
            "credit_key": credit_key,
            "block": "seven_day_block",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_holm_path(path: str) -> dict[str, Any] | None:
    try:
        decoded = json.loads(path)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        return decoded
    parts = path.split(".")
    if len(parts) >= 5:
        metric = parts[3]
        credit_key = None
        if metric.startswith("net_credit_"):
            raw_metric = metric
            metric = "net_credit"
            credit_key = raw_metric.removeprefix("net_credit_")
        return {
            "family": "secondary_model_choice",
            "group": parts[0],
            "subset": parts[1],
            "comparison": parts[2],
            "metric": metric,
            "credit_key": credit_key,
            "block": parts[-1],
        }
    return None


def apply_holm(feature_group_results: dict[str, Any], adjusted: dict[str, float]) -> None:
    for path, value in adjusted.items():
        target = parse_holm_path(path)
        if not target or target.get("family") != "secondary_model_choice":
            continue
        group = str(target.get("group") or "")
        subset = str(target.get("subset") or "")
        comparison = str(target.get("comparison") or "")
        result = feature_group_results.get(group) or {}
        container = result if subset == "all_pairs" else (result.get("same_actual_width_subset") or {})
        item = (container.get("comparisons") or {}).get(comparison)
        if not isinstance(item, dict):
            continue
        if target.get("metric") == "actual_payout_btc_saving":
            item.setdefault("actual_payout_btc_saving", {}).setdefault("seven_day_block", {})["holm_adjusted_p"] = value
        elif target.get("metric") == "net_credit":
            credit_key = str(target.get("credit_key") or "")
            item.setdefault("by_credit_scenario", {}).setdefault(credit_key, {}).setdefault("seven_day_block", {})["holm_adjusted_p"] = value


def rebuild_feature_group_result(result: dict[str, Any], records: Sequence[dict[str, Any]], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    metadata_keys = {
        "status",
        "feature_group",
        "artifact_hash",
        "selected_basis",
        "baseline_note",
        "selected",
        "reason",
    }
    metadata = {key: copy.deepcopy(value) for key, value in result.items() if key in metadata_keys}
    rebuilt = summarize_model_records(records, credit_scenarios)
    rebuilt.update(metadata)
    rebuilt["same_actual_width_subset"] = summarize_model_records(
        [record for record in records if record.get("same_actual_width")],
        credit_scenarios,
    )
    rebuilt["sample_records"] = list(records)
    return rebuilt


def normalize_choice_record(record: dict[str, Any], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    normalized = copy.deepcopy(record)
    baselines = normalized.get("baselines") or {}
    for value_item in [normalized.get("model_choice"), *baselines.values()]:
        if isinstance(value_item, dict):
            normalize_value_item(value_item, baselines, credit_scenarios)
    return normalized


def normalize_value_item(value_item: dict[str, Any], baselines: dict[str, Any], credit_scenarios: Sequence[float]) -> None:
    net_by_credit = value_item.setdefault("net_btc_by_credit", {})
    win_by_credit = value_item.setdefault("net_win_by_credit", {})
    breakeven_by_credit = value_item.setdefault("net_breakeven_by_credit", {})
    for credit in credit_scenarios:
        credit_key = f"{float(credit):.2f}"
        if value_item.get("side") == "equal_put_call_risk":
            win_by_credit[credit_key] = mean_or_none(
                [
                    net_win_from_values((baselines or {}).get("fixed_put") or {}, credit_key),
                    net_win_from_values((baselines or {}).get("fixed_call") or {}, credit_key),
                ]
            )
            breakeven_by_credit[credit_key] = mean_or_none(
                [
                    net_breakeven_from_values((baselines or {}).get("fixed_put") or {}, credit_key),
                    net_breakeven_from_values((baselines or {}).get("fixed_call") or {}, credit_key),
                ]
            )
            continue
        value = finite_float((net_by_credit or {}).get(credit_key))
        if value is not None:
            win_by_credit[credit_key] = 1.0 if value > EPSILON else 0.0
            breakeven_by_credit[credit_key] = 1.0 if abs(value) <= EPSILON else 0.0


def dedupe_preserve_order(items: Sequence[Any]) -> list[Any]:
    result = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def episode_transition_comparisons(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    by_episode_side: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in filtered:
        side = model.canonical_side(row.get("side"))
        episode_id = str(row.get("episode_id") or row.get("active_episode_id") or "")
        kind = str(row.get("observation_kind") or "unknown").lower()
        if side and episode_id:
            by_episode_side[(episode_id, side)][kind].append(row)
    transitions = {}
    for target_kind in ("cooldown", "plus15", "plus30"):
        records = []
        missing: dict[str, int] = defaultdict(int)
        for (episode_id, side), kinds in by_episode_side.items():
            shock = earliest_row(kinds.get("shock") or [])
            target = earliest_row(kinds.get(target_kind) or [])
            if shock is None:
                missing["missing_shock"] += 1
            elif target is None:
                missing[f"missing_{target_kind}"] += 1
            else:
                records.append(transition_record(episode_id, side, shock, target, target_kind))
        transitions[f"shock_to_{target_kind}"] = summarize_transition_records(records, missing)
    return {
        "scope": "Same episode and side only. Entry, DTE, and legs may differ; without synchronized quotes this is price/payout timing evidence, not full economic proof.",
        "transitions": transitions,
    }


def transition_record(episode_id: str, side: str, shock: dict[str, Any], target: dict[str, Any], target_kind: str) -> dict[str, Any]:
    shock_entry = finite_float(shock.get("entry_price"))
    target_entry = finite_float(target.get("entry_price"))
    shock_payout = actual_payout_btc(shock)
    target_payout = actual_payout_btc(target)
    return {
        "episode_id": episode_id,
        "side": side,
        "delivery_date": str(target.get("delivery_date") or shock.get("delivery_date")),
        "target_kind": target_kind,
        "shock_row_id": shock.get("row_id"),
        "target_row_id": target.get("row_id"),
        "shock_entry_ms": shock.get("entry_ms"),
        "target_entry_ms": target.get("entry_ms"),
        "entry_price_change_pct": pct_change(shock_entry, target_entry),
        "dte_hours_change": diff(finite_float(shock.get("dte_hours")), finite_float(target.get("dte_hours"))),
        "actual_width_change": diff(finite_float(shock.get("actual_width")), finite_float(target.get("actual_width"))),
        "short_strike_change": diff(finite_float(shock.get("short_strike")), finite_float(target.get("short_strike"))),
        "long_strike_change": diff(finite_float(shock.get("long_strike")), finite_float(target.get("long_strike"))),
        "loss_normalized_change": diff(model.target_loss(shock), model.target_loss(target)),
        "payout_btc_saving_vs_shock": None if shock_payout is None or target_payout is None else shock_payout - target_payout,
    }


def summarize_transition_records(records: Sequence[dict[str, Any]], missing: dict[str, int]) -> dict[str, Any]:
    return {
        "paired_records": len(records),
        "paired_delivery_dates": independent_dates(records),
        "missing_counts": dict(sorted(missing.items())),
        "by_side": {side: transition_value_summary([record for record in records if record.get("side") == side]) for side in ("put_credit", "call_credit")},
        "all": transition_value_summary(records),
        "sample_records": list(records),
    }


def transition_value_summary(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "delivery_dates": independent_dates(records),
        "entry_price_change_pct": value_summary(records, [record.get("entry_price_change_pct") for record in records]),
        "dte_hours_change": value_summary(records, [record.get("dte_hours_change") for record in records]),
        "actual_width_change": value_summary(records, [record.get("actual_width_change") for record in records]),
        "payout_btc_saving_vs_shock": paired_bootstrap_summary(records, "payout_btc_saving_vs_shock"),
    }


def paired_bootstrap_summary(records: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = finite_float(record.get(key))
        if value is not None:
            by_day[str(record.get("delivery_date"))].append(value)
    return bootstrap_day_and_week(day_means(by_day))


def paired_side_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, dict[str, Any]]]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in filtered:
        side = model.canonical_side(row.get("side"))
        if side in {"put_credit", "call_credit"}:
            grouped[row_pair_key(row)][side] = row
    return [sides for sides in grouped.values() if "put_credit" in sides and "call_credit" in sides]


def row_pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("event_family"),
        row.get("episode_id"),
        row.get("observation_kind"),
        row.get("as_of_ms"),
        row.get("entry_ms"),
        row.get("expiry_ms"),
        row.get("target_width"),
    )


def same_actual_width_pair(pair: dict[str, dict[str, Any]]) -> bool:
    put_width = finite_float(pair["put_credit"].get("actual_width"))
    call_width = finite_float(pair["call_credit"].get("actual_width"))
    return put_width is not None and call_width is not None and abs(put_width - call_width) <= 1e-9


def coverage_summary(rows: Sequence[dict[str, Any]], pairs: Sequence[dict[str, dict[str, Any]]]) -> dict[str, Any]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    paired_keys = {row_pair_key(pair["put_credit"]) for pair in pairs}
    return {
        "locked_rows": len(filtered),
        "locked_delivery_dates": len({str(row.get("delivery_date")) for row in filtered}),
        "put_call_pairs": len(pairs),
        "paired_delivery_dates": len({str(pair["put_credit"].get("delivery_date")) for pair in pairs}),
        "unpaired_rows": sum(1 for row in filtered if row_pair_key(row) not in paired_keys),
        "same_actual_width_pairs": sum(1 for pair in pairs if same_actual_width_pair(pair)),
        "different_actual_width_pairs": sum(1 for pair in pairs if not same_actual_width_pair(pair)),
        "observation_kind_counts": count_by(filtered, lambda row: str(row.get("observation_kind") or "unknown").lower()),
    }


def net_scenario_summary(records: Sequence[dict[str, Any]], name: str, credit: float) -> dict[str, Any]:
    clean_records: list[dict[str, Any]] = []
    clean_values: list[float] = []
    clean_components: list[list[tuple[float, float]]] = []
    for record in records:
        value = net_value_at(record, name, credit)
        components = net_components_at(record, name, credit)
        if value is None or not components:
            continue
        clean_records.append(record)
        clean_values.append(value)
        clean_components.append(components)
    if not clean_records:
        return {
            "records": 0,
            "delivery_dates": 0,
            "primary_weighting": "date_equal",
            "mean": None,
            "win_rate": None,
            "breakeven_rate": None,
            "tail_loss_p95_abs": None,
        }
    date_values: list[float] = []
    date_weights: list[float] = []
    raw_values: list[float] = []
    raw_weights: list[float] = []
    for record_weight, components in zip(equal_delivery_weights(clean_records), clean_components):
        total_component_weight = sum(component_weight for _, component_weight in components) or 1.0
        for component_value, component_weight in components:
            fraction = component_weight / total_component_weight
            date_values.append(component_value)
            date_weights.append(record_weight * fraction)
            raw_values.append(component_value)
            raw_weights.append(fraction)
    date_equal = weighted_stats(
        date_values,
        date_weights,
        win_threshold=0.0,
        win_direction="gt",
        win_values=[1.0 if value > EPSILON else 0.0 for value in date_values],
        breakeven_values=[1.0 if abs(value) <= EPSILON else 0.0 for value in date_values],
    )
    raw = weighted_stats(
        raw_values,
        raw_weights,
        win_threshold=0.0,
        win_direction="gt",
        win_values=[1.0 if value > EPSILON else 0.0 for value in raw_values],
        breakeven_values=[1.0 if abs(value) <= EPSILON else 0.0 for value in raw_values],
    )
    mean_summary = value_summary(clean_records, clean_values)
    return {
        "records": len(clean_records),
        "delivery_dates": independent_dates(clean_records),
        "primary_weighting": "date_equal",
        "mean": mean_summary["mean"],
        "win_rate": date_equal.get("win_rate"),
        "breakeven_rate": date_equal.get("breakeven_rate"),
        "tail_loss_p95_abs": date_equal.get("tail_loss_p95_abs"),
        "date_equal": {**date_equal, "mean": mean_summary["mean"]},
        "raw": {**raw, "mean": mean_summary["raw"]["mean"]},
        "tail_loss_basis": (
            "single_side_component_distribution; equal_put_call_risk uses 0.5 weight per side, not average-net tail"
            if name == "equal_put_call_risk"
            or any((record.get("model_choice") or {}).get("side") == "equal_put_call_risk" for record in clean_records if name == "model_choice")
            else "selected_or_fixed_single_side_distribution"
        ),
    }


def value_summary(
    records: Sequence[dict[str, Any]],
    values: Sequence[Any],
    *,
    win_threshold: float | None = None,
    win_direction: str = "ge",
    win_values: Sequence[Any] | None = None,
    breakeven_values: Sequence[Any] | None = None,
) -> dict[str, Any]:
    wins = list(win_values) if win_values is not None else [None] * len(records)
    breakevens = list(breakeven_values) if breakeven_values is not None else [None] * len(records)
    finite_pairs = [
        (record, float(value), finite_float(win), finite_float(breakeven))
        for record, value, win, breakeven in zip(records, values, wins, breakevens)
        if finite_float(value) is not None
    ]
    if not finite_pairs:
        return {
            "records": 0,
            "delivery_dates": 0,
            "primary_weighting": "date_equal",
            "mean": None,
            "win_rate": None,
            "breakeven_rate": None,
            "tail_loss_p95_abs": None,
        }
    clean_records = [record for record, _, _, _ in finite_pairs]
    clean_values = [value for _, value, _, _ in finite_pairs]
    clean_wins = [win for _, _, win, _ in finite_pairs]
    clean_breakevens = [breakeven for _, _, _, breakeven in finite_pairs]
    date_equal = weighted_stats(
        clean_values,
        equal_delivery_weights(clean_records),
        win_threshold=win_threshold,
        win_direction=win_direction,
        win_values=clean_wins,
        breakeven_values=clean_breakevens,
    )
    raw = weighted_stats(
        clean_values,
        [1.0] * len(clean_values),
        win_threshold=win_threshold,
        win_direction=win_direction,
        win_values=clean_wins,
        breakeven_values=clean_breakevens,
    )
    return {
        "records": len(clean_values),
        "delivery_dates": independent_dates(clean_records),
        "primary_weighting": "date_equal",
        "mean": date_equal["mean"],
        "win_rate": date_equal.get("win_rate"),
        "breakeven_rate": date_equal.get("breakeven_rate"),
        "tail_loss_p95_abs": date_equal.get("tail_loss_p95_abs"),
        "date_equal": date_equal,
        "raw": raw,
    }


def weighted_stats(
    values: Sequence[float],
    weights: Sequence[float],
    *,
    win_threshold: float | None,
    win_direction: str,
    win_values: Sequence[float | None] | None = None,
    breakeven_values: Sequence[float | None] | None = None,
) -> dict[str, Any]:
    total = sum(weights) or float(len(values))
    mean = sum(value * weight for value, weight in zip(values, weights)) / total
    result: dict[str, Any] = {"mean": mean}
    if win_values is not None:
        valid = [(float(win), weight) for win, weight in zip(win_values, weights) if finite_float(win) is not None]
        valid_total = sum(weight for _, weight in valid)
        result["win_rate"] = (sum(win * weight for win, weight in valid) / valid_total) if valid_total else None
        if breakeven_values is not None:
            be_valid = [
                (float(breakeven), weight)
                for breakeven, weight in zip(breakeven_values, weights)
                if finite_float(breakeven) is not None
            ]
            be_total = sum(weight for _, weight in be_valid)
            result["breakeven_rate"] = (sum(breakeven * weight for breakeven, weight in be_valid) / be_total) if be_total else None
    if win_threshold is not None:
        if win_direction == "ge":
            win_weight = sum(weight for value, weight in zip(values, weights) if value >= win_threshold)
            losses = [(-value, weight) for value, weight in zip(values, weights) if value < win_threshold]
        elif win_direction == "gt":
            win_weight = sum(weight for value, weight in zip(values, weights) if value > win_threshold)
            losses = [(-value, weight) for value, weight in zip(values, weights) if value < win_threshold]
        elif win_direction == "lt":
            win_weight = sum(weight for value, weight in zip(values, weights) if value < win_threshold)
            losses = [(value, weight) for value, weight in zip(values, weights) if value > win_threshold]
        elif win_direction == "le":
            win_weight = sum(weight for value, weight in zip(values, weights) if value <= win_threshold)
            losses = [(value, weight) for value, weight in zip(values, weights) if value > win_threshold]
        else:
            raise ValueError(f"unsupported win_direction: {win_direction}")
        if win_values is None:
            result["win_rate"] = win_weight / total
        result["tail_loss_p95_abs"] = weighted_quantile(losses, 0.95)
    return result


def weighted_quantile(values_and_weights: Sequence[tuple[float, float]], q: float) -> float | None:
    values = sorted((float(value), float(weight)) for value, weight in values_and_weights if weight > 0.0 and math.isfinite(float(value)))
    if not values:
        return None
    target = sum(weight for _, weight in values) * q
    cumulative = 0.0
    for value, weight in values:
        cumulative += weight
        if cumulative >= target:
            return value
    return values[-1][0]


def day_means(by_day_values: dict[str, Iterable[float]]) -> dict[str, float]:
    result = {}
    for day, values in by_day_values.items():
        finite = [float(value) for value in values if finite_float(value) is not None]
        if finite:
            result[str(day)] = sum(finite) / len(finite)
    return dict(sorted(result.items()))


def equal_delivery_weights(records: Sequence[dict[str, Any]]) -> list[float]:
    grouped: dict[str, int] = defaultdict(int)
    for record in records:
        grouped[str(record.get("delivery_date"))] += 1
    if not records or not grouped:
        return []
    day_weight = len(records) / len(grouped)
    return [day_weight / grouped[str(record.get("delivery_date"))] for record in records]


def value_at(record: dict[str, Any], name: str, key: str) -> float | None:
    values = record.get("model_choice") if name == "model_choice" else (record.get("baselines") or {}).get(name)
    return finite_float((values or {}).get(key))


def net_value_at(record: dict[str, Any], name: str, credit: float) -> float | None:
    values = record.get("model_choice") if name == "model_choice" else (record.get("baselines") or {}).get(name)
    return finite_float(((values or {}).get("net_btc_by_credit") or {}).get(f"{float(credit):.2f}"))


def net_components_at(record: dict[str, Any], name: str, credit: float) -> list[tuple[float, float]]:
    credit_key = f"{float(credit):.2f}"
    values = record.get("model_choice") if name == "model_choice" else (record.get("baselines") or {}).get(name)
    if not isinstance(values, dict):
        return []
    if values.get("side") == "equal_put_call_risk":
        baselines = record.get("baselines") or {}
        components = [
            finite_float((((baselines.get("fixed_put") or {}).get("net_btc_by_credit") or {}).get(credit_key))),
            finite_float((((baselines.get("fixed_call") or {}).get("net_btc_by_credit") or {}).get(credit_key))),
        ]
        return [(value, 0.5) for value in components if value is not None]
    value = finite_float(((values.get("net_btc_by_credit") or {}).get(credit_key)))
    return [] if value is None else [(value, 1.0)]


def net_win_at(record: dict[str, Any], name: str, credit: float) -> float | None:
    values = record.get("model_choice") if name == "model_choice" else (record.get("baselines") or {}).get(name)
    return net_win_from_values(values or {}, f"{float(credit):.2f}")


def net_breakeven_at(record: dict[str, Any], name: str, credit: float) -> float | None:
    values = record.get("model_choice") if name == "model_choice" else (record.get("baselines") or {}).get(name)
    return net_breakeven_from_values(values or {}, f"{float(credit):.2f}")


def net_win_from_values(values: dict[str, Any], credit_key: str) -> float | None:
    stored = finite_float(((values or {}).get("net_win_by_credit") or {}).get(credit_key))
    if stored is not None:
        return stored
    net = finite_float(((values or {}).get("net_btc_by_credit") or {}).get(credit_key))
    return None if net is None else (1.0 if net > EPSILON else 0.0)


def net_breakeven_from_values(values: dict[str, Any], credit_key: str) -> float | None:
    stored = finite_float(((values or {}).get("net_breakeven_by_credit") or {}).get(credit_key))
    if stored is not None:
        return stored
    net = finite_float(((values or {}).get("net_btc_by_credit") or {}).get(credit_key))
    return None if net is None else (1.0 if abs(net) <= EPSILON else 0.0)


def actual_payout_btc(row: dict[str, Any]) -> float | None:
    direct = finite_float(row.get("payout_btc"))
    if direct is not None:
        return direct
    normalized = model.target_loss(row)
    width = width_value_btc(row)
    if normalized is None or width is None:
        return None
    return float(normalized) * width


def width_value_btc(row: dict[str, Any]) -> float | None:
    width = finite_float(row.get("actual_width"))
    entry = finite_float(row.get("entry_price"))
    if width is None or entry is None or entry <= 0.0:
        return None
    return width / entry


def finite_float(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mean_or_none(values: Iterable[Any]) -> float | None:
    finite = [float(value) for value in values if finite_float(value) is not None]
    return sum(finite) / len(finite) if finite else None


def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or abs(old) < EPSILON:
        return None
    return (new - old) / old


def diff(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    return new - old


def earliest_row(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: (finite_float(row.get("as_of_ms")) or math.inf, finite_float(row.get("entry_ms")) or math.inf))[0]


def independent_dates(records: Sequence[dict[str, Any]]) -> int:
    return len({str(record.get("delivery_date")) for record in records})


def count_by(rows: Sequence[dict[str, Any]], key_fn: Any) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(key_fn(row))] += 1
    return dict(sorted(counts.items()))


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_markdown(path: Path, text: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite and path.read_text(encoding="utf-8") != text:
        raise SupplementaryComparisonError(f"supplementary markdown already exists: {path}")
    path.write_text(text, encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    coverage = payload.get("coverage") or {}
    lines = [
        "# Astra 联合研究 v1 补充对照",
        "",
        f"- 生成时间：{payload.get('created_at_utc')}",
        f"- 共同 Put/Call 样本：{coverage.get('put_call_pairs')} 组，独立交割日 {coverage.get('paired_delivery_dates')}",
        f"- 同实际宽度样本：{coverage.get('same_actual_width_pairs')} 组；不同实际宽度样本：{coverage.get('different_actual_width_pairs')} 组",
        "",
        "## 修正后的主 MAE 对照",
        "",
    ]
    for name, item in ((payload.get("corrected_primary_comparisons") or {}).get("comparisons") or {}).items():
        week = item.get("seven_day_block") or {}
        lines.append(f"- {name}：七日块差值 {format_number(week.get('observed_difference'))}，Holm p {format_number(week.get('holm_adjusted_p'))}")
    lines.extend(["", "## 模型选侧对照", "", "模型按预测 BTC 赔付较小的一侧选侧；没有同步历史报价，因此这是赔付风险对照，不是完整净 edge 证明。", ""])
    for group, result in (payload.get("feature_group_results") or {}).items():
        lines.append(f"### {group}")
        lines.append("")
        if result.get("status") != "available":
            lines.append(f"- 状态：{result.get('status')} / {result.get('reason')}")
        else:
            lines.append(f"- 选侧分布：{json.dumps(result.get('selected_side_counts'), ensure_ascii=False)}")
            for name, item in (result.get("comparisons") or {}).items():
                week = ((item.get("actual_payout_btc_saving") or {}).get("seven_day_block") or {})
                lines.append(f"- {name}：七日块 BTC 赔付节省均值 {format_number(week.get('observed_difference'))}")
        lines.append("")
    lines.extend(["## 择时配对", "", "shock 到 cooldown / plus15 / plus30 只比较同 episode、同侧的价格与赔付变化；各自入场、DTE 和腿可能变化。", ""])
    for name, item in (((payload.get("timing_transitions") or {}).get("transitions") or {}).items()):
        week = ((((item.get("all") or {}).get("payout_btc_saving_vs_shock") or {}).get("seven_day_block") or {}))
        lines.append(f"- {name}：配对 {item.get('paired_records')} 条，七日块赔付节省均值 {format_number(week.get('observed_difference'))}")
    lines.append("")
    return "\n".join(lines)


def format_number(value: object) -> str:
    number = finite_float(value)
    return "暂无" if number is None else f"{number:.10g}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build sealed supplementary comparisons for Astra joint research.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--repair-existing", action="store_true", help="Repair the current supplementary report from stored sample_records without rerunning inference.")
    args = parser.parse_args(argv)
    if args.repair_existing:
        payload = repair_existing_supplementary_report(args.root, overwrite=args.overwrite)
    else:
        payload = run_supplementary_comparisons(args.root, overwrite=args.overwrite)
    print(json.dumps({"schema": payload["schema"], "coverage": payload["coverage"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
