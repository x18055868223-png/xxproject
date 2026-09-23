"""Experiment driver for Astra joint research v1.

This file is research-side orchestration only.  It deliberately separates the
model-selection seal from the later locked-test command so 2023 outcomes are not
read while selecting models.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from astra_joint_contract import FEATURE_GROUPS, PROTOCOL
from astra_joint_sources import digest, save
import astra_joint_dataset as dataset
import astra_joint_inference as inference
import astra_joint_model as model
import astra_joint_pipeline as pipeline


SELECTION_SEAL_SCHEMA = "astra_joint_selection_seal@1.0.0"
LOCKED_TEST_REPORT_SCHEMA = "astra_joint_locked_test_report@1.0.0"
SUMMARY_REPORT_SCHEMA = "astra_joint_experiment_report@1.0.0"
TRAIN_YEARS = tuple(PROTOCOL["train_years"])
SELECTION_YEARS = tuple(PROTOCOL["selection_years"])
LOCKED_TEST_YEARS = tuple(PROTOCOL["test_years"])
MODEL_YEARS = TRAIN_YEARS + SELECTION_YEARS
PRIMARY_FEATURE_GROUPS = ("statistical", "mechanism", "joint")


class ExperimentStateError(RuntimeError):
    """Raised when an experiment stage would violate the sealed workflow."""


class training_lock:
    """Small cross-process lock for one training job per research root."""

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = Path(models_dir)
        self.path = self.models_dir / "training.lock"
        self.fd: int | None = None

    def __enter__(self) -> "training_lock":
        self.models_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ExperimentStateError(f"training lock already exists: {self.path}") from exc
        payload = json.dumps({"created_at_utc": now_utc(), "pid": os.getpid()}, ensure_ascii=False).encode("utf-8")
        os.write(self.fd, payload)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def run_train(
    root: Path,
    *,
    include_catboost: bool = True,
    feature_groups: Sequence[str] = PRIMARY_FEATURE_GROUPS,
) -> dict[str, Any]:
    """Train/select using 2020-2022 labels and 2023 identity-only purge rows."""

    root = Path(root)
    models_dir = root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    result_path = models_dir / "selection_result.json"
    seal_path = models_dir / "selection_seal.json"
    with training_lock(models_dir):
        if seal_path.exists() or result_path.exists():
            raise ExperimentStateError("selection already sealed; keep the recorded model-selection result")
        _ensure_outcomes(root, MODEL_YEARS)
        started = time.monotonic()
        rss_start = process_rss_mb()
        labeled_rows = pipeline.joined_rows(root, MODEL_YEARS, primary_only=True)
        locked_identity_rows = read_candidate_identity_rows(root, LOCKED_TEST_YEARS)
        selection_input_rows = labeled_rows + locked_identity_rows
        selection_result = model.train_select(
            selection_input_rows,
            feature_groups=feature_groups,
            include_catboost=include_catboost,
            holdout_purge_splits={"selection", "locked_test"},
        )
        elapsed = time.monotonic() - started
        rss_end = process_rss_mb()
        selection_result["experiment_context"] = {
            "schema": SELECTION_SEAL_SCHEMA,
            "stage": "model_selection",
            "label_years": list(MODEL_YEARS),
            "train_years": list(TRAIN_YEARS),
            "selection_years": list(SELECTION_YEARS),
            "locked_test_identity_years": list(LOCKED_TEST_YEARS),
            "locked_test_targets_read": False,
            "selection_targets_read": True,
            "primary_width": PROTOCOL["primary_width"],
            "candidate_count_expected": expected_candidate_count(feature_groups, include_catboost=include_catboost),
            "candidate_count_observed": len(selection_result.get("candidate_artifacts") or []),
            "elapsed_seconds": round(elapsed, 6),
            "rss_start_mb": rss_start,
            "rss_end_mb": rss_end,
            "rss_delta_mb": round(rss_end - rss_start, 6) if rss_start is not None and rss_end is not None else None,
        }
        save(result_path, selection_result, immutable=True)
        seal = build_selection_seal(root, selection_result, result_path, locked_identity_rows)
        save(seal_path, seal, immutable=True)
        return seal


def run_test(root: Path, *, credit_scenarios: Sequence[float] | None = None) -> dict[str, Any]:
    """Open the explicit locked-test stage and evaluate sealed artifacts."""

    root = Path(root)
    models_dir = root / "models"
    result_path = models_dir / "selection_result.json"
    seal_path = models_dir / "selection_seal.json"
    if not seal_path.exists() or not result_path.exists():
        raise ExperimentStateError("selection_seal.json and selection_result.json are required before locked testing")
    selection_result, selection_seal, selection_validation = load_and_validate_selection(root, result_path, seal_path)
    _ensure_outcomes(root, LOCKED_TEST_YEARS)
    rows = pipeline.joined_rows(root, LOCKED_TEST_YEARS, primary_only=True)
    credit_scenarios = list(credit_scenarios or PROTOCOL["net_credit_scenarios"])

    model_evaluations = evaluate_feature_group_artifacts(selection_result, rows, credit_scenarios=credit_scenarios)
    primary_comparisons = evaluate_primary_comparisons(selection_result, rows, credit_scenarios=credit_scenarios)
    baseline_results = {
        "by_credit_scenario": {
            f"{float(credit):.2f}": baseline_summary(rows, credit=float(credit))
            for credit in credit_scenarios
        },
        "note": "Net credit scenarios are width-normalized research assumptions, not historical executable quotes.",
    }
    split_results = split_summaries(rows, credit_scenarios)
    coverage = coverage_summary(rows, selection_result)
    report = {
        "schema": LOCKED_TEST_REPORT_SCHEMA,
        "created_at_utc": now_utc(),
        "selection_result_sha256": digest(result_path),
        "selection_seal_sha256": digest(seal_path),
        "selection_validation": selection_validation,
        "protocol": protocol_subset(),
        "scope": {
            "locked_test_years": list(LOCKED_TEST_YEARS),
            "primary_width": PROTOCOL["primary_width"],
            "same_records_for_model_comparisons": True,
            "no_model_refit_or_candidate_search": True,
            "selection_sealed_at_utc": selection_seal.get("sealed_at_utc"),
            "timing_group_note": "Timing groups are descriptive; DTE, option availability, and quote conditions may be confounded.",
        },
        "coverage": coverage,
        "model_evaluations": model_evaluations,
        "primary_comparisons": primary_comparisons,
        "baselines": baseline_results,
        "event_clock_and_timing": split_results,
    }
    test_path = models_dir / "locked_test_report.json"
    save(test_path, report, immutable=True)
    return report


def run_report(root: Path, *, out: Path | None = None) -> dict[str, Any]:
    """Create a compact JSON and Markdown summary from sealed selection/test files."""

    root = Path(root)
    models_dir = root / "models"
    selection_path = models_dir / "selection_result.json"
    seal_path = models_dir / "selection_seal.json"
    test_path = models_dir / "locked_test_report.json"
    if not selection_path.exists() or not seal_path.exists():
        raise ExperimentStateError("selection files are required before reporting")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    locked_test = json.loads(test_path.read_text(encoding="utf-8")) if test_path.exists() else None
    payload = {
        "schema": SUMMARY_REPORT_SCHEMA,
        "created_at_utc": now_utc(),
        "selection_status": selection.get("status"),
        "selection_note": selection.get("selection_note"),
        "selected": selection.get("selected"),
        "best_by_feature_group": {
            group: (selection.get("best_artifacts", {}).get(group, {}) or {}).get("selected")
            for group in PRIMARY_FEATURE_GROUPS
        },
        "locked_test_status": "available" if locked_test else "not_run",
        "locked_test_coverage": (locked_test or {}).get("coverage"),
        "primary_comparisons": (locked_test or {}).get("primary_comparisons"),
        "baselines": (locked_test or {}).get("baselines"),
    }
    save(models_dir / "experiment_summary.json", payload, immutable=False)
    markdown = render_markdown_summary(payload)
    markdown_path = out or (models_dir / "experiment_summary.md")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    return payload


def expected_candidate_count(feature_groups: Sequence[str], *, include_catboost: bool) -> int:
    gam_count = len(PROTOCOL["gam_logistic_C"]) * len(PROTOCOL["gam_gamma_alpha"])
    catboost_count = len(PROTOCOL["catboost_depths"]) if include_catboost else 0
    return len(feature_groups) * (gam_count + catboost_count)


def read_candidate_identity_rows(root: Path, years: Iterable[int]) -> list[dict[str, Any]]:
    """Read locked-test candidate identities without joining outcomes."""

    root = Path(root)
    rows: list[dict[str, Any]] = []
    for year in years:
        path = root / "decisions" / f"candidates-{year}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for raw in csv.DictReader(stream):
                row = dict(raw)
                row["split"] = "locked_test"
                for key in ("as_of_ms", "entry_ms", "expiry_ms", "target_width", "actual_width", "entry_price"):
                    row[key] = inference.finite_float(row.get(key))
                for key in ("loss_normalized", "payout_btc", "settlement_price"):
                    row.pop(key, None)
                rows.append(row)
    return rows


def build_selection_seal(
    root: Path,
    selection_result: dict[str, Any],
    result_path: Path,
    locked_identity_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    candidate_files = {
        str(year): file_digest_or_none(root / "decisions" / f"candidates-{year}.csv")
        for year in MODEL_YEARS + LOCKED_TEST_YEARS
    }
    outcome_files = {
        str(year): file_digest_or_none(root / "outcomes" / f"outcomes-{year}.csv")
        for year in MODEL_YEARS
    }
    return {
        "schema": SELECTION_SEAL_SCHEMA,
        "sealed_at_utc": now_utc(),
        "status": selection_result.get("status"),
        "protocol_sha256": file_digest_or_none(root / "protocol.json"),
        "protocol": protocol_subset(),
        "data": {
            "label_years": list(MODEL_YEARS),
            "locked_test_identity_years": list(LOCKED_TEST_YEARS),
            "locked_test_targets_read": False,
            "candidate_files": candidate_files,
            "outcome_files": outcome_files,
            "locked_test_identity_rows": len(locked_identity_rows),
        },
        "selection_result_sha256": digest(result_path),
        "candidate_count_expected": selection_result["experiment_context"]["candidate_count_expected"],
        "candidate_count_observed": selection_result["experiment_context"]["candidate_count_observed"],
        "selected_artifact_hash": selection_result.get("selected_artifact_hash"),
        "best_artifact_hashes": {
            group: (selection_result.get("best_artifacts", {}).get(group, {}) or {}).get("selected_artifact_hash")
            for group in PRIMARY_FEATURE_GROUPS
        },
        "sealed_primary_comparisons": selection_result.get("sealed_primary_comparisons", []),
    }


def load_and_validate_selection(
    root: Path,
    result_path: Path,
    seal_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection_result = json.loads(result_path.read_text(encoding="utf-8"))
    selection_seal = json.loads(seal_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if selection_seal.get("schema") != SELECTION_SEAL_SCHEMA:
        errors.append("selection_seal_schema_mismatch")
    recorded_result_hash = selection_seal.get("selection_result_sha256")
    actual_result_hash = digest(result_path)
    if recorded_result_hash != actual_result_hash:
        errors.append("selection_result_hash_mismatch")
    recorded_protocol_hash = selection_seal.get("protocol_sha256")
    actual_protocol_hash = file_digest_or_none(root / "protocol.json")
    if recorded_protocol_hash != actual_protocol_hash:
        errors.append("protocol_hash_mismatch")
    if selection_seal.get("protocol") != protocol_subset():
        errors.append("protocol_payload_mismatch")
    data = selection_seal.get("data") or {}
    for year, expected in (data.get("candidate_files") or {}).items():
        path = root / "decisions" / f"candidates-{year}.csv"
        if expected != file_digest_or_none(path):
            errors.append(f"candidate_file_hash_mismatch:{year}")
    for year, expected in (data.get("outcome_files") or {}).items():
        path = root / "outcomes" / f"outcomes-{year}.csv"
        if expected != file_digest_or_none(path):
            errors.append(f"outcome_file_hash_mismatch:{year}")
    if data.get("locked_test_targets_read") is not False:
        errors.append("selection_declares_locked_test_targets_read")
    expected_candidates = selection_seal.get("candidate_count_expected")
    observed_candidates = selection_seal.get("candidate_count_observed")
    actual_candidates = len(selection_result.get("candidate_artifacts") or [])
    if observed_candidates != actual_candidates:
        errors.append("candidate_artifact_count_mismatch")
    if expected_candidates is not None and int(expected_candidates) != actual_candidates:
        errors.append("candidate_artifact_expected_count_mismatch")
    artifact_errors, artifact_checked = validate_candidate_artifact_hashes(selection_result)
    errors.extend(artifact_errors)
    validation = {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "selection_result_sha256": actual_result_hash,
        "protocol_sha256": actual_protocol_hash,
        "candidate_count_expected": expected_candidates,
        "candidate_count_observed": actual_candidates,
        "candidate_artifacts_checked": artifact_checked,
    }
    if errors:
        raise ExperimentStateError("selection validation failed: " + ", ".join(errors))
    return selection_result, selection_seal, validation


def validate_candidate_artifact_hashes(selection_result: dict[str, Any]) -> tuple[list[str], int]:
    """Verify every persisted candidate artifact and every selected reference."""

    errors: list[str] = []
    checked = 0
    candidate_artifacts = selection_result.get("candidate_artifacts") or []
    for index, item in enumerate(candidate_artifacts):
        if not isinstance(item, dict):
            errors.append(f"candidate_artifact_entry_invalid:{index}")
            continue
        artifact = item.get("artifact")
        recorded_hash = item.get("artifact_hash")
        if artifact is None:
            if recorded_hash not in (None, ""):
                errors.append(f"candidate_artifact_missing_but_hash_present:{index}")
            continue
        if not isinstance(artifact, dict):
            errors.append(f"candidate_artifact_payload_invalid:{index}")
            continue
        checked += 1
        computed_hash = model.stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
        embedded_hash = artifact.get("artifact_hash")
        if embedded_hash != computed_hash:
            errors.append(f"candidate_artifact_embedded_hash_mismatch:{index}")
        if recorded_hash != embedded_hash:
            errors.append(f"candidate_artifact_recorded_hash_mismatch:{index}")

    for label, artifact, recorded_hash in selected_artifact_references(selection_result):
        if artifact is None and recorded_hash not in (None, ""):
            errors.append(f"selected_artifact_missing_but_hash_present:{label}")
            continue
        if artifact is None:
            continue
        if not isinstance(artifact, dict):
            errors.append(f"selected_artifact_payload_invalid:{label}")
            continue
        computed_hash = model.stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
        embedded_hash = artifact.get("artifact_hash")
        if embedded_hash != computed_hash:
            errors.append(f"selected_artifact_embedded_hash_mismatch:{label}")
        if recorded_hash != embedded_hash:
            errors.append(f"selected_artifact_recorded_hash_mismatch:{label}")
    return errors, checked


def selected_artifact_references(selection_result: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    references: list[tuple[str, Any, Any]] = [
        ("global", selection_result.get("selected_artifact"), selection_result.get("selected_artifact_hash")),
    ]
    for group, pick in (selection_result.get("best_artifacts") or {}).items():
        if isinstance(pick, dict):
            references.append((f"best:{group}", pick.get("selected_artifact"), pick.get("selected_artifact_hash")))
    return references


def evaluate_feature_group_artifacts(
    selection_result: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    credit_scenarios: Sequence[float],
) -> dict[str, Any]:
    results = {}
    for group, pick in (selection_result.get("best_artifacts") or {}).items():
        artifact = (pick or {}).get("selected_artifact")
        if artifact:
            results[group] = model.evaluate_artifact(artifact, rows, split_names={"locked_test"})
            results[group]["artifact_hash"] = (pick or {}).get("selected_artifact_hash")
            results[group]["candidate"] = (pick or {}).get("selected")
            results[group]["side_choice"] = model_side_choice_summary(artifact, rows, credit_scenarios=credit_scenarios)
        else:
            results[group] = {
                "status": "unavailable",
                "reason": "no_selected_artifact_for_feature_group",
                "candidate": (pick or {}).get("selected"),
            }
    return results


def evaluate_primary_comparisons(
    selection_result: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    credit_scenarios: Sequence[float],
) -> dict[str, Any]:
    best = selection_result.get("best_artifacts") or {}
    comparisons = {}
    p_values = {}
    for comparison in PROTOCOL["primary_comparisons"]:
        if comparison == "joint_minus_statistical":
            left, right = "joint", "statistical"
        elif comparison == "joint_minus_mechanism":
            left, right = "joint", "mechanism"
        else:
            comparisons[comparison] = {"status": "unknown_comparison"}
            continue
        left_artifact = (best.get(left) or {}).get("selected_artifact")
        right_artifact = (best.get(right) or {}).get("selected_artifact")
        if not left_artifact or not right_artifact:
            comparisons[comparison] = {
                "status": "unavailable",
                "left_feature_group": left,
                "right_feature_group": right,
            }
            continue
        left_errors = delivery_day_abs_errors(left_artifact, rows)
        right_errors = delivery_day_abs_errors(right_artifact, rows)
        boot = model.bootstrap_metric_difference(left_errors, right_errors)
        comparisons[comparison] = {
            **boot,
            "left_feature_group": left,
            "right_feature_group": right,
            "metric": "expected_loss_mae_by_delivery_day",
            "side_choice_actual_btc": compare_model_side_choices(
                left_artifact,
                right_artifact,
                rows,
                credit_scenarios=credit_scenarios,
                same_actual_width_only=False,
            ),
            "side_choice_same_actual_width": compare_model_side_choices(
                left_artifact,
                right_artifact,
                rows,
                credit_scenarios=credit_scenarios,
                same_actual_width_only=True,
            ),
        }
        if boot.get("status") == "available" and boot.get("p_value_two_sided_centered") is not None:
            p_values[comparison] = float(boot["p_value_two_sided_centered"])
    adjusted = model.holm_adjust(p_values) if p_values else {}
    for name, value in adjusted.items():
        comparisons[name]["holm_adjusted_p"] = value
    return comparisons


def delivery_day_abs_errors(artifact: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    eval_rows = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    predictions = inference.predict_rows(artifact, eval_rows)
    errors = [
        abs(float(prediction["expected_loss_normalized"]) - float(model.target_loss(row) or 0.0))
        for prediction, row in zip(predictions, eval_rows)
    ]
    return delivery_day_metric(eval_rows, errors)


def model_side_choice_summary(
    artifact: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    credit_scenarios: Sequence[float],
) -> dict[str, Any]:
    pairs = paired_side_rows(rows)
    predictions = predictions_by_row_id(artifact, [row for pair in pairs for row in pair.values()])
    btc_records = choice_records(pairs, predictions, selector="predicted_btc")
    normalized_records = choice_records(pairs, predictions, selector="predicted_normalized")
    return {
        "basis": "Model side choice is diagnostic only. BTC ranking accounts for actual_width/entry_price; normalized ranking is shown separately.",
        "predicted_btc_ranking": summarize_choice_records(btc_records, credit_scenarios),
        "predicted_normalized_ranking": summarize_choice_records(normalized_records, credit_scenarios),
        "same_actual_width_pairs": summarize_choice_records(
            [record for record in btc_records if record["same_actual_width"]],
            credit_scenarios,
        ),
        "coverage": {
            "put_call_pairs": len(pairs),
            "same_actual_width_pairs": sum(1 for record in btc_records if record["same_actual_width"]),
            "different_actual_width_pairs": sum(1 for record in btc_records if not record["same_actual_width"]),
        },
    }


def compare_model_side_choices(
    left_artifact: dict[str, Any],
    right_artifact: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    credit_scenarios: Sequence[float],
    same_actual_width_only: bool,
) -> dict[str, Any]:
    pairs = paired_side_rows(rows)
    if same_actual_width_only:
        pairs = [pair for pair in pairs if same_actual_width_pair(pair)]
    flattened = [row for pair in pairs for row in pair.values()]
    left_predictions = predictions_by_row_id(left_artifact, flattened)
    right_predictions = predictions_by_row_id(right_artifact, flattened)
    left_records = {record["pair_key"]: record for record in choice_records(pairs, left_predictions, selector="predicted_btc")}
    right_records = {record["pair_key"]: record for record in choice_records(pairs, right_predictions, selector="predicted_btc")}
    common_keys = sorted(set(left_records) & set(right_records))
    if not common_keys:
        return {"status": "unavailable", "paired_choices": 0, "same_actual_width_only": same_actual_width_only}
    result: dict[str, Any] = {
        "status": "available",
        "same_actual_width_only": same_actual_width_only,
        "paired_choices": len(common_keys),
        "metric_direction": "positive means the left model has higher net BTC result or lower BTC payout",
        "actual_payout_btc_saving": bootstrap_records(
            common_keys,
            left_records,
            right_records,
            value_fn=lambda left, right: float(right["chosen_actual_payout_btc"]) - float(left["chosen_actual_payout_btc"]),
        ),
        "by_credit_scenario": {},
    }
    for credit in credit_scenarios:
        result["by_credit_scenario"][f"{float(credit):.2f}"] = bootstrap_records(
            common_keys,
            left_records,
            right_records,
            value_fn=lambda left, right, credit=float(credit): float(left["net_btc_by_credit"][credit])
            - float(right["net_btc_by_credit"][credit]),
        )
    return result


def paired_side_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, dict[str, Any]]]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in filtered:
        side = model.canonical_side(row.get("side"))
        if side in {"put_credit", "call_credit"}:
            grouped[row_pair_key(row)][side] = row
    return [
        sides
        for sides in grouped.values()
        if "put_credit" in sides and "call_credit" in sides
    ]


def predictions_by_row_id(artifact: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    predictions = inference.predict_rows(artifact, rows)
    return {str(row.get("row_id")): prediction for row, prediction in zip(rows, predictions)}


def choice_records(
    pairs: Sequence[dict[str, dict[str, Any]]],
    predictions: dict[str, dict[str, Any]],
    *,
    selector: str,
) -> list[dict[str, Any]]:
    records = []
    for pair in pairs:
        put = pair["put_credit"]
        call = pair["call_credit"]
        put_prediction = predictions.get(str(put.get("row_id")))
        call_prediction = predictions.get(str(call.get("row_id")))
        if not put_prediction or not call_prediction:
            continue
        if selector == "predicted_btc":
            put_score = predicted_btc_loss(put_prediction, put)
            call_score = predicted_btc_loss(call_prediction, call)
        elif selector == "predicted_normalized":
            put_score = float(put_prediction["expected_loss_normalized"])
            call_score = float(call_prediction["expected_loss_normalized"])
        else:
            raise ValueError(f"unknown side-choice selector: {selector}")
        if abs(put_score - call_score) <= 1e-15:
            chosen = put
            other = call
            tie = True
        elif put_score < call_score:
            chosen = put
            other = call
            tie = False
        else:
            chosen = call
            other = put
            tie = False
        chosen_payout_btc = actual_payout_btc(chosen)
        other_payout_btc = actual_payout_btc(other)
        if chosen_payout_btc is None or other_payout_btc is None:
            continue
        width_btc = width_value_btc(chosen)
        records.append(
            {
                "pair_key": row_pair_key(chosen),
                "delivery_date": str(chosen.get("delivery_date")),
                "chosen_side": model.canonical_side(chosen.get("side")),
                "selector": selector,
                "selection_tie": tie,
                "same_actual_width": same_actual_width_pair(pair),
                "chosen_actual_width": model.finite_float(chosen.get("actual_width")),
                "other_actual_width": model.finite_float(other.get("actual_width")),
                "chosen_actual_payout_btc": chosen_payout_btc,
                "other_actual_payout_btc": other_payout_btc,
                "chosen_actual_loss_normalized": float(model.target_loss(chosen) or 0.0),
                "other_actual_loss_normalized": float(model.target_loss(other) or 0.0),
                "chosen_width_btc": width_btc,
                "net_btc_by_credit": {
                    float(credit): (float(credit) * width_btc) - chosen_payout_btc
                    for credit in PROTOCOL["net_credit_scenarios"]
                    if width_btc is not None
                },
            }
        )
    return records


def summarize_choice_records(records: Sequence[dict[str, Any]], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    side_counts: dict[str, int] = defaultdict(int)
    for record in records:
        side_counts[str(record["chosen_side"])] += 1
    lower_payout = [
        1.0 if float(record["chosen_actual_payout_btc"]) < float(record["other_actual_payout_btc"]) else 0.0
        for record in records
        if abs(float(record["chosen_actual_payout_btc"]) - float(record["other_actual_payout_btc"])) > 1e-15
    ]
    return {
        "records": len(records),
        "delivery_dates": len({record["delivery_date"] for record in records}),
        "side_counts": dict(sorted(side_counts.items())),
        "selection_ties": sum(1 for record in records if record["selection_tie"]),
        "lower_actual_btc_payout_rate": sum(lower_payout) / len(lower_payout) if lower_payout else None,
        "mean_actual_payout_btc": mean_record_value(records, "chosen_actual_payout_btc"),
        "date_equal_mean_actual_payout_btc": date_equal_record_mean(records, "chosen_actual_payout_btc"),
        "by_credit_scenario": {
            f"{float(credit):.2f}": payoff_summary_from_record_values(
                [
                    (record, float(record["net_btc_by_credit"][float(credit)]))
                    for record in records
                    if float(credit) in record["net_btc_by_credit"]
                    and math.isfinite(float(record["net_btc_by_credit"][float(credit)]))
                ],
            )
            for credit in credit_scenarios
        },
    }


def bootstrap_records(
    keys: Sequence[tuple[Any, ...]],
    left_records: dict[tuple[Any, ...], dict[str, Any]],
    right_records: dict[tuple[Any, ...], dict[str, Any]],
    *,
    value_fn: Any,
) -> dict[str, Any]:
    left_by_day_values: dict[str, list[float]] = defaultdict(list)
    right_zero_by_day: dict[str, list[float]] = defaultdict(list)
    for key in keys:
        left = left_records[key]
        right = right_records[key]
        day = str(left["delivery_date"])
        left_by_day_values[day].append(float(value_fn(left, right)))
        right_zero_by_day[day].append(0.0)
    left_by_day = {day: sum(values) / len(values) for day, values in left_by_day_values.items()}
    right_by_day = {day: 0.0 for day in right_zero_by_day}
    return model.bootstrap_metric_difference(left_by_day, right_by_day)


def predicted_btc_loss(prediction: dict[str, Any], row: dict[str, Any]) -> float:
    return float(prediction["expected_loss_normalized"]) * width_value_btc(row)


def actual_payout_btc(row: dict[str, Any]) -> float | None:
    direct = model.finite_float(row.get("payout_btc"))
    if direct is not None:
        return direct
    normalized = model.target_loss(row)
    if normalized is None:
        return None
    return float(normalized) * width_value_btc(row)


def width_value_btc(row: dict[str, Any]) -> float:
    width = model.finite_float(row.get("actual_width"))
    entry_price = model.finite_float(row.get("entry_price"))
    if width is None or entry_price is None or entry_price <= 0.0:
        return math.nan
    return width / entry_price


def same_actual_width_pair(pair: dict[str, dict[str, Any]]) -> bool:
    put_width = model.finite_float(pair["put_credit"].get("actual_width"))
    call_width = model.finite_float(pair["call_credit"].get("actual_width"))
    return put_width is not None and call_width is not None and abs(put_width - call_width) <= 1e-9


def mean_record_value(records: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [float(record[key]) for record in records if record.get(key) is not None and math.isfinite(float(record[key]))]
    return sum(values) / len(values) if values else None


def date_equal_record_mean(records: Sequence[dict[str, Any]], key: str) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = record.get(key)
        if value is not None and math.isfinite(float(value)):
            grouped[str(record["delivery_date"])].append(float(value))
    if not grouped:
        return None
    return sum(sum(values) / len(values) for values in grouped.values()) / len(grouped)


def payoff_summary_from_record_values(record_values: Sequence[tuple[dict[str, Any], float]]) -> dict[str, Any]:
    paired_records = [record for record, _ in record_values]
    finite_values = [float(value) for _, value in record_values]
    date_equal = weighted_payoff_summary(finite_values, equal_delivery_weights_for_records(paired_records))
    raw = weighted_payoff_summary(finite_values, [1.0] * len(finite_values))
    return {
        "records": len(finite_values),
        "primary_weighting": "date_equal",
        "win_rate": date_equal["win_rate"],
        "mean_net_btc": date_equal["mean_net"],
        "date_equal": date_equal,
        "raw": raw,
    }


def baseline_summary(rows: Sequence[dict[str, Any]], *, credit: float) -> dict[str, Any]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    return {
        "credit_width_fraction": credit,
        "fixed_put": payoff_summary([row for row in filtered if model.canonical_side(row.get("side")) == "put_credit"], credit),
        "fixed_call": payoff_summary([row for row in filtered if model.canonical_side(row.get("side")) == "call_credit"], credit),
        "equal_put_call_risk": payoff_summary(filtered, credit),
    }


def payoff_summary(rows: Sequence[dict[str, Any]], credit: float) -> dict[str, Any]:
    values = [float(credit) - float(model.target_loss(row) or 0.0) for row in rows]
    by_day = delivery_day_metric(rows, values)
    date_equal = weighted_payoff_summary(values, equal_delivery_weights_for_rows(rows))
    raw = weighted_payoff_summary(values, [1.0] * len(values))
    return {
        "rows": len(rows),
        "delivery_dates": len({str(row.get("delivery_date")) for row in rows}),
        "primary_weighting": "date_equal",
        "win_rate": date_equal["win_rate"],
        "mean_net_normalized": date_equal["mean_net"],
        "average_win_normalized": date_equal["average_win"],
        "average_loss_abs_normalized": date_equal["average_loss_abs"],
        "win_loss_ratio": date_equal["win_loss_ratio"],
        "tail_loss_p95_abs_normalized": date_equal["tail_loss_p95_abs"],
        "date_equal": date_equal,
        "raw": raw,
        "by_delivery_day_mean_net": by_day,
    }


def split_summaries(rows: Sequence[dict[str, Any]], credit_scenarios: Sequence[float]) -> dict[str, Any]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    return {
        "note": "Event/clock and timing buckets are descriptive research cuts; DTE, quote availability, and calendar regime are not isolated causes.",
        "event_vs_clock": grouped_baselines(filtered, lambda row: event_clock_bucket(row), credit_scenarios),
        "observation_kind": grouped_baselines(filtered, lambda row: observation_kind_bucket(row), credit_scenarios),
        "timing_bjt": grouped_baselines(filtered, lambda row: timing_bucket_bjt(row), credit_scenarios),
    }


def grouped_baselines(
    rows: Sequence[dict[str, Any]],
    key_fn: Any,
    credit_scenarios: Sequence[float],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    return {
        key: {
            "rows": len(group_rows),
            "delivery_dates": len({str(row.get("delivery_date")) for row in group_rows}),
            "dte_hours": numeric_range([model.finite_float(row.get("dte_hours")) for row in group_rows]),
            "by_credit_scenario": {
                f"{float(credit):.2f}": {
                    "fixed_put": payoff_summary(
                        [row for row in group_rows if model.canonical_side(row.get("side")) == "put_credit"],
                        float(credit),
                    ),
                    "fixed_call": payoff_summary(
                        [row for row in group_rows if model.canonical_side(row.get("side")) == "call_credit"],
                        float(credit),
                    ),
                    "equal_put_call_risk": payoff_summary(group_rows, float(credit)),
                }
                for credit in credit_scenarios
            },
        }
        for key, group_rows in sorted(groups.items())
    }


def event_clock_bucket(row: dict[str, Any]) -> str:
    if str(row.get("observation_kind") or "").lower() == "clock":
        return "clock_inside_episode" if truthy(row.get("in_episode")) else "clock_outside_episode"
    return "event"


def observation_kind_bucket(row: dict[str, Any]) -> str:
    kind = str(row.get("observation_kind") or "unknown").strip().lower()
    if kind == "clock":
        return event_clock_bucket(row)
    if kind in {"shock", "cooldown", "plus15", "plus30"}:
        return kind
    return kind or "unknown"


def truthy(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on", "active"}


def timing_bucket_bjt(row: dict[str, Any]) -> str:
    entry_ms = model.finite_float(row.get("entry_ms"))
    if entry_ms is None:
        return "unknown"
    moment = datetime.fromtimestamp(entry_ms / 1000, timezone.utc) + timedelta(hours=8)
    minutes = moment.hour * 60 + moment.minute
    if 4 * 60 <= minutes < 8 * 60:
        return "美股收盘后_04_08"
    if 8 * 60 <= minutes < 15 * 60:
        return "亚盘_08_15"
    if 15 * 60 <= minutes < 20 * 60:
        return "欧盘_15_20"
    if 20 * 60 <= minutes < 21 * 60 + 30:
        return "美盘前核心窗_20_2130"
    return "美盘中_2130_04"


def coverage_summary(rows: Sequence[dict[str, Any]], selection_result: dict[str, Any]) -> dict[str, Any]:
    filtered = model.filter_rows(rows, split_names={"locked_test"}, primary_width_only=True)
    pairs = defaultdict(set)
    for row in filtered:
        pairs[row_pair_key(row)].add(model.canonical_side(row.get("side")))
    comparable_pairs = sum(1 for sides in pairs.values() if {"put_credit", "call_credit"} <= sides)
    side_pairs = paired_side_rows(filtered)
    return {
        "locked_rows": len(filtered),
        "locked_delivery_dates": len({str(row.get("delivery_date")) for row in filtered}),
        "observation_pairs": len(pairs),
        "put_call_comparable_pairs": comparable_pairs,
        "same_actual_width_pairs": sum(1 for pair in side_pairs if same_actual_width_pair(pair)),
        "different_actual_width_pairs": sum(1 for pair in side_pairs if not same_actual_width_pair(pair)),
        "candidate_count_observed_at_selection": len(selection_result.get("candidate_artifacts") or []),
        "best_feature_groups": sorted((selection_result.get("best_artifacts") or {}).keys()),
    }


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


def delivery_day_metric(rows: Sequence[dict[str, Any]], values: Sequence[float]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, values):
        grouped[str(row.get("delivery_date"))].append(float(value))
    return {day: sum(day_values) / len(day_values) for day, day_values in sorted(grouped.items())}


def equal_delivery_weights_for_rows(rows: Sequence[dict[str, Any]]) -> list[float]:
    if not rows:
        return []
    grouped: dict[str, int] = defaultdict(int)
    for row in rows:
        grouped[str(row.get("delivery_date"))] += 1
    day_weight = len(rows) / len(grouped) if grouped else 1.0
    return [day_weight / grouped[str(row.get("delivery_date"))] for row in rows]


def equal_delivery_weights_for_records(records: Sequence[dict[str, Any]]) -> list[float]:
    if not records:
        return []
    grouped: dict[str, int] = defaultdict(int)
    for record in records:
        grouped[str(record.get("delivery_date"))] += 1
    day_weight = len(records) / len(grouped) if grouped else 1.0
    return [day_weight / grouped[str(record.get("delivery_date"))] for record in records]


def weighted_payoff_summary(values: Sequence[float], weights: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "win_rate": None,
            "mean_net": None,
            "average_win": None,
            "average_loss_abs": None,
            "win_loss_ratio": None,
            "tail_loss_p95_abs": None,
        }
    total_weight = sum(float(weight) for weight in weights) or float(len(values))
    win_weight = sum(float(weight) for value, weight in zip(values, weights) if value >= 0.0)
    mean_net = sum(float(value) * float(weight) for value, weight in zip(values, weights)) / total_weight
    wins = [(float(value), float(weight)) for value, weight in zip(values, weights) if value >= 0.0]
    losses = [(-float(value), float(weight)) for value, weight in zip(values, weights) if value < 0.0]
    average_win = weighted_mean_pairs(wins)
    average_loss = weighted_mean_pairs(losses)
    return {
        "win_rate": win_weight / total_weight,
        "mean_net": mean_net,
        "average_win": average_win,
        "average_loss_abs": average_loss,
        "win_loss_ratio": average_win / average_loss if average_win is not None and average_loss and average_loss > 0.0 else None,
        "tail_loss_p95_abs": weighted_quantile_pairs(losses, 0.95),
    }


def weighted_mean_pairs(values_and_weights: Sequence[tuple[float, float]]) -> float | None:
    if not values_and_weights:
        return None
    total_weight = sum(weight for _, weight in values_and_weights)
    if total_weight <= 0:
        return sum(value for value, _ in values_and_weights) / len(values_and_weights)
    return sum(value * weight for value, weight in values_and_weights) / total_weight


def weighted_quantile_pairs(values_and_weights: Sequence[tuple[float, float]], q: float) -> float | None:
    if not values_and_weights:
        return None
    ordered = sorted((float(value), float(weight)) for value, weight in values_and_weights if weight > 0.0)
    if not ordered:
        return None
    target = sum(weight for _, weight in ordered) * q
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _ensure_outcomes(root: Path, years: Sequence[int]) -> None:
    missing = [year for year in years if not (root / "outcomes" / f"outcomes-{year}.csv").exists()]
    if missing:
        dataset.build_outcomes(root, missing)


def protocol_subset() -> dict[str, Any]:
    keys = [
        "schema",
        "seed",
        "train_years",
        "selection_years",
        "test_years",
        "previously_used_years",
        "primary_width",
        "gam_logistic_C",
        "gam_gamma_alpha",
        "catboost_depths",
        "catboost_learning_rate",
        "catboost_max_iterations",
        "catboost_early_stopping_rounds",
        "min_training_delivery_days",
        "min_positive_delivery_days",
        "net_credit_scenarios",
        "primary_comparisons",
    ]
    return {key: PROTOCOL[key] for key in keys}


def file_digest_or_none(path: Path) -> str | None:
    return digest(path) if path.exists() else None


def process_rss_mb() -> float | None:
    try:
        import psutil  # type: ignore

        return round(psutil.Process(os.getpid()).memory_info().rss / 1_048_576, 6)
    except Exception:
        pass
    try:
        import resource  # type: ignore

        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if os.name == "posix":
            rss /= 1024.0
        return round(rss, 6)
    except Exception:
        return None


def numeric_range(values: Iterable[float | None]) -> dict[str, float | None]:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return {"min": None, "max": None, "mean": None}
    return {"min": min(finite), "max": max(finite), "mean": sum(finite) / len(finite)}


def quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def render_markdown_summary(payload: dict[str, Any]) -> str:
    selected = payload.get("selected") or {}
    lines = [
        "# Astra 联合研究 v1 实验摘要",
        "",
        f"- 生成时间：{payload.get('created_at_utc')}",
        f"- 模型选择状态：{payload.get('selection_status')}",
        f"- 选择说明：{payload.get('selection_note') or '暂无'}",
        f"- 全局选择：{selected.get('model_family') or '暂无'} / {selected.get('feature_group') or '暂无'}",
        f"- 锁定测试状态：{payload.get('locked_test_status')}",
        "",
        "## 主要比较",
        "",
    ]
    comparisons = payload.get("primary_comparisons") or {}
    if comparisons:
        for name, item in comparisons.items():
            lines.append(
                f"- {name}: {item.get('status')}；差值 {item.get('observed_difference')}；Holm p {item.get('holm_adjusted_p')}"
            )
    else:
        lines.append("- 尚未运行锁定测试。")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "本摘要只引用已经封存的模型选择和显式测试结果。时间分组结果用于描述，不单独解释为时段因果优势。",
            "",
        ]
    )
    return "\n".join(lines)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sealed Astra joint-research experiment stages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--root", required=True, type=Path)
    train_parser.add_argument("--no-catboost", action="store_true")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--root", required=True, type=Path)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--root", required=True, type=Path)
    report_parser.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command == "train":
        print(json.dumps(run_train(args.root, include_catboost=not args.no_catboost), ensure_ascii=False), flush=True)
        return 0
    if args.command == "test":
        print(json.dumps(run_test(args.root), ensure_ascii=False), flush=True)
        return 0
    if args.command == "report":
        print(json.dumps(run_report(args.root, out=args.out), ensure_ascii=False), flush=True)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
