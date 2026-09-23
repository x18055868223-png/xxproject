"""Independent acceptance checks for frozen Astra v1.5 state-audit runs.

This module only validates runner outputs. It does not train models, replay
market data, or tune thresholds after seeing results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


METHODS = ("WINDOWS", "DYNAMIC", "MIX2", "HMM2", "HMM_RESET")
PRIMARY_COMPARATORS = ("DYNAMIC", "MIX2")
TASKS = ("exit", "structure")
SIDES = ("put", "call")
SPLITS = ("fit", "cal", "eval")
YEARS = (2022, 2023, 2024, 2025)
EPS = 1e-9


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(_jsonable(obj), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return value if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value)


def _bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    lowered = series.astype(str).str.lower()
    return lowered.isin(("true", "1", "yes"))


def _finite_nonnegative(series: pd.Series) -> bool:
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    return bool(np.isfinite(values).all() and (values >= 0).all())


class Acceptance:
    def __init__(self) -> None:
        self.failures: list[dict[str, Any]] = []

    def fail(self, check: str, message: str, layer: str = "engineering", **context: Any) -> None:
        self.failures.append({"check": check, "layer": layer, "message": message, "context": context})

    def ok(self) -> bool:
        return not self.failures

    def layer_ok(self, layer: str) -> bool:
        return not any(f["layer"] == layer for f in self.failures)


def _layer_count(failures: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"engineering": 0, "model": 0, "research": 0}
    for failure in failures:
        layer = failure.get("layer", "engineering")
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def _require_columns(acc: Acceptance, frame: pd.DataFrame, path: str, columns: list[str]) -> bool:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        acc.fail("missing_columns", f"{path} missing required columns", path=path, missing=missing)
        return False
    return True


def _read_csv(acc: Acceptance, path: Path) -> pd.DataFrame:
    if not path.exists():
        acc.fail("missing_file", "required CSV is missing", path=str(path))
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:  # pragma: no cover - exercised through CLI failures
        acc.fail("csv_read_error", "failed to read CSV", path=str(path), error=str(exc))
        return pd.DataFrame()


def _load_required(run_dir: Path, acc: Acceptance) -> dict[str, Any]:
    files: dict[str, Any] = {
        "predictions": _read_csv(acc, run_dir / "predictions.csv"),
        "state_support": _read_csv(acc, run_dir / "state_support.csv"),
        "scenario_metrics": _read_csv(acc, run_dir / "scenario_metrics.csv"),
        "predictive_metrics": _read_csv(acc, run_dir / "predictive_metrics.csv"),
    }
    for name in ("fit_log.json", "scenario_contrasts.json", "predictive_contrasts.json"):
        path = run_dir / name
        if not path.exists():
            acc.fail("missing_file", "required JSON is missing", path=str(path))
            files[name] = []
        else:
            try:
                files[name] = _read_json(path)
            except Exception as exc:
                acc.fail("json_read_error", "failed to read JSON", path=str(path), error=str(exc))
                files[name] = []
    state_files = {}
    for year in YEARS:
        path = run_dir / f"states_{year}.json"
        if not path.exists():
            acc.fail("missing_file", "required state JSON is missing", path=str(path))
            continue
        try:
            state_files[year] = _read_json(path)
        except Exception as exc:
            acc.fail("json_read_error", "failed to read state JSON", path=str(path), error=str(exc), year=year)
    files["states"] = state_files
    files["scenario_ledgers"] = sorted(run_dir.glob("*_scenario_*.csv"))
    if not files["scenario_ledgers"]:
        acc.fail("missing_file", "no scenario ledger CSV files found", pattern="*_scenario_*.csv")
    return files


def check_source_identity(acc: Acceptance, research: Path, run_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    result = {
        "protocol_seal": False,
        "source_correction_02_seal": False,
        "execution_identity": False,
        "passed": False,
    }
    protocol_path = research / "protocol_v15.json"
    manifest_path = research / "source_manifest.json"
    protocol_seal_path = research / "protocol_seal.json"
    if not protocol_seal_path.exists():
        acc.fail("protocol_seal_missing", "protocol_seal.json is required", path=str(protocol_seal_path))
    else:
        try:
            seal = _read_json(protocol_seal_path)
            if not protocol_path.exists() or _digest(protocol_path) != seal.get("protocol_sha256"):
                acc.fail("protocol_seal_hash_mismatch", "protocol_v15.json hash does not match protocol_seal.json", path=str(protocol_path))
            elif not manifest_path.exists() or _digest(manifest_path) != seal.get("source_manifest_sha256"):
                acc.fail("source_manifest_seal_hash_mismatch", "source_manifest.json hash does not match protocol_seal.json", path=str(manifest_path))
            else:
                result["protocol_seal"] = True
        except Exception as exc:
            acc.fail("protocol_seal_read_error", "failed to verify protocol seal", path=str(protocol_seal_path), error=str(exc))

    correction_path = research / "source_correction_02.json"
    correction_seal_path = research / "source_correction_02_seal.json"
    correction: dict[str, Any] = {}
    correction_sha = None
    if not correction_path.exists() or not correction_seal_path.exists():
        acc.fail("source_correction_02_missing", "source_correction_02.json and source_correction_02_seal.json are required for corrected acceptance", path=str(research))
    else:
        try:
            correction = _read_json(correction_path)
            correction_seal = _read_json(correction_seal_path)
            correction_sha = correction_seal.get("sha256")
            if _digest(correction_path) != correction_sha:
                acc.fail("source_correction_02_hash_mismatch", "source_correction_02.json hash does not match its seal", path=str(correction_path))
            elif correction.get("parent_protocol_sha256") and protocol_seal_path.exists():
                seal = _read_json(protocol_seal_path)
                if correction.get("parent_protocol_sha256") != seal.get("protocol_sha256"):
                    acc.fail("source_correction_02_parent_mismatch", "source correction parent protocol hash does not match protocol seal", path=str(correction_path))
                else:
                    result["source_correction_02_seal"] = True
            else:
                result["source_correction_02_seal"] = True
        except Exception as exc:
            acc.fail("source_correction_02_read_error", "failed to verify source correction seal", path=str(correction_path), error=str(exc))

    identity_path = run_dir / "execution_identity.json"
    if not identity_path.exists():
        acc.fail("execution_identity_missing", "run execution_identity.json is required", path=str(identity_path))
    else:
        try:
            identity = _read_json(identity_path)
            if protocol_seal_path.exists():
                seal = _read_json(protocol_seal_path)
                if identity.get("protocol_sha256") != seal.get("protocol_sha256"):
                    acc.fail("execution_identity_protocol_mismatch", "run protocol hash does not match protocol seal", path=str(identity_path))
            if correction_sha is not None and identity.get("source_correction_sha256") != correction_sha:
                acc.fail("execution_identity_source_correction_mismatch", "run source correction hash does not match source_correction_02_seal", path=str(identity_path))
            source_code = identity.get("source_code") or {}
            if not isinstance(source_code, dict) or not source_code:
                acc.fail("execution_identity_source_code_missing", "run execution identity must record source_code hashes", path=str(identity_path))
            else:
                expected = correction.get("source_files") or {}
                normalized_expected = {str(k).replace("\\", "/"): v for k, v in expected.items()}
                required_suffixes = ("tools/astra_state_audit_v15.py", "tools/astra_state_v15.py")
                for suffix in required_suffixes:
                    matches = [v for k, v in source_code.items() if str(k).replace("\\", "/").endswith(suffix)]
                    if not matches:
                        acc.fail("execution_identity_source_hash_missing", "run source_code is missing a required execution source hash", source_file=suffix)
                    elif suffix in normalized_expected and normalized_expected[suffix] not in matches:
                        acc.fail("execution_identity_source_hash_mismatch", "run source_code hash does not match source correction", source_file=suffix, expected=normalized_expected[suffix], observed=matches)
                for path_key, observed in source_code.items():
                    normalized = str(path_key).replace("\\", "/")
                    matched = [sha for rel, sha in normalized_expected.items() if normalized.endswith(rel)]
                    if matched and observed not in matched:
                        acc.fail("execution_identity_source_hash_mismatch", "run source_code hash does not match corrected source hash", source_file=path_key, expected=matched, observed=observed)
                if all(f["check"].startswith("execution_identity_") is False for f in acc.failures):
                    result["execution_identity"] = True
        except Exception as exc:
            acc.fail("execution_identity_read_error", "failed to verify run execution identity", path=str(identity_path), error=str(exc))

    result["passed"] = result["protocol_seal"] and result["source_correction_02_seal"] and result["execution_identity"]
    return result


def check_predictions(acc: Acceptance, predictions: pd.DataFrame) -> dict[str, Any]:
    result = {"rows": int(len(predictions)), "passed": False}
    if predictions.empty:
        acc.fail("predictions_empty", "predictions.csv is empty")
        return result
    required = ["task", "row_id", "side", "entry_ms", "expiry_ms", "delivery_date", "target", "year", *METHODS]
    if not _require_columns(acc, predictions, "predictions.csv", required):
        return result
    dup = predictions.duplicated(["task", "row_id"])
    if bool(dup.any()):
        acc.fail("prediction_duplicate_task_row", "task,row_id identity must be unique", rows=int(dup.sum()))
    for column in ("target", *METHODS):
        if not _finite_nonnegative(predictions[column]):
            acc.fail("prediction_invalid_value", "prediction/target must be finite and nonnegative", column=column)
    if "hmm_p1" in predictions.columns:
        p = pd.to_numeric(predictions["hmm_p1"], errors="coerce").to_numpy(float)
        if not (np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all()):
            acc.fail("prediction_invalid_hmm_probability", "hmm_p1 must be finite and within [0,1]")
    for row in predictions[["year", "entry_ms", "expiry_ms", "task", "row_id"]].itertuples(index=False):
        year = int(row.year)
        eval_start = _ms(f"{year}-01-01")
        eval_end = _ms(f"{year + 1}-01-01")
        if int(row.entry_ms) < eval_start or int(row.entry_ms) >= eval_end or int(row.expiry_ms) >= eval_end:
            acc.fail(
                "prediction_eval_split_cutoff",
                "prediction row violates evaluation entry/expiry cutoff",
                task=row.task,
                row_id=row.row_id,
                year=year,
            )
    result["passed"] = acc.ok()
    return result


def check_fit_log(acc: Acceptance, fit_log: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"rows": len(fit_log), "calibration_days_min": None, "passed": False}
    if not fit_log:
        acc.fail("fit_log_empty", "fit_log.json has no records")
        return result
    cal_days: list[int] = []
    expected = {(task, side, year, method) for task in TASKS for side in SIDES for year in YEARS for method in METHODS}
    seen: list[tuple[str, str, int, str]] = []
    for item in fit_log:
        context = {k: item.get(k) for k in ("task", "side", "year", "method")}
        year = int(item.get("year"))
        key = (str(item.get("task")), str(item.get("side")), year, str(item.get("method")))
        seen.append(key)
        expected_cal_start = _ms(f"{year - 1}-10-01")
        expected_eval_start = _ms(f"{year}-01-01")
        if int(item.get("cal_start", -1)) != expected_cal_start or int(item.get("eval_start", -1)) != expected_eval_start:
            acc.fail("fit_log_split_boundary", "fit/cal/eval split boundary does not match protocol", **context)
        if int(item.get("fit_max_expiry", 10**30)) >= int(item.get("cal_start", -1)):
            acc.fail("fit_expiry_cutoff", "fit expiry must be strictly before calibration start", **context)
        if int(item.get("cal_max_expiry", 10**30)) >= int(item.get("eval_start", -1)):
            acc.fail("cal_expiry_cutoff", "calibration expiry must be strictly before evaluation start", **context)
        calibration = item.get("calibration") or {}
        days = int(calibration.get("days", 0))
        cal_days.append(days)
        if days < 30 or calibration.get("qualified") is not True:
            acc.fail("head_calibration_unqualified", "head calibration must have >=30 expiry days and be qualified", layer="model", days=days, **context)
    seen_set = set(seen)
    missing = sorted(expected - seen_set)
    duplicate_count = len(seen) - len(seen_set)
    if missing:
        acc.fail("fit_log_coverage_missing", "fit_log.json must cover 2 tasks x 2 sides x 4 years x 5 methods", missing=missing[:20], missing_count=len(missing))
    if duplicate_count:
        acc.fail("fit_log_coverage_duplicate", "fit_log.json has duplicate task/side/year/method records", duplicate_count=duplicate_count)
    result["calibration_days_min"] = min(cal_days) if cal_days else None
    result["passed"] = not any(f["check"] in {"fit_log_split_boundary", "fit_expiry_cutoff", "cal_expiry_cutoff", "head_calibration_unqualified", "fit_log_coverage_missing", "fit_log_coverage_duplicate"} for f in acc.failures)
    return result


def check_state_support(acc: Acceptance, support: pd.DataFrame) -> dict[str, Any]:
    result = {"rows": int(len(support)), "passed": False}
    if support.empty:
        acc.fail("state_support_empty", "state_support.csv is empty")
        return result
    required = ["year", "scope", "split", "state", "fraction", "days"]
    if not _require_columns(acc, support, "state_support.csv", required):
        return result
    support = support.copy()
    support["year"] = pd.to_numeric(support["year"], errors="coerce").astype("Int64")
    support["state"] = pd.to_numeric(support["state"], errors="coerce").astype("Int64")
    support["fraction"] = pd.to_numeric(support["fraction"], errors="coerce")
    support["days"] = pd.to_numeric(support["days"], errors="coerce")

    market = support[support["scope"].eq("market")]
    expected_market = {(year, split, state) for year in YEARS for split in SPLITS for state in (0, 1)}
    seen_market = {
        (int(row.year), str(row.split), int(row.state))
        for row in market[["year", "split", "state"]].itertuples(index=False)
        if not pd.isna(row.year) and not pd.isna(row.state)
    }
    missing_market = sorted(expected_market - seen_market)
    if missing_market:
        acc.fail("state_support_market_coverage_missing", "state_support.csv must include all market year/split/state rows", missing=missing_market[:20], missing_count=len(missing_market))
    for year in YEARS:
        for state in (0, 1):
            fit = market[(market["year"].eq(year)) & (market["state"].eq(state)) & (market["split"].eq("fit"))]
            if fit.empty or float(fit["days"].iloc[0]) < 30:
                acc.fail("state_market_fit_days", "each HMM state needs >=30 fit market days", layer="model", year=year, state=state)
            for split in ("fit", "cal", "eval"):
                row = market[(market["year"].eq(year)) & (market["state"].eq(state)) & (market["split"].eq(split))]
                if row.empty or not np.isfinite(float(row["fraction"].iloc[0])) or float(row["fraction"].iloc[0]) < 0.05:
                    acc.fail("state_market_occupancy", "each HMM state needs >=5% market occupancy per split", layer="model", year=year, state=state, split=split)

    task_rows = support[~support["scope"].eq("market")]
    if "side" not in task_rows.columns:
        acc.fail("missing_columns", "state_support.csv task rows need side column", path="state_support.csv", missing=["side"])
    else:
        expected_task = {
            (year, task, side, split, state)
            for year in YEARS
            for task in TASKS
            for side in SIDES
            for split in SPLITS
            for state in (0, 1)
        }
        seen_task = {
            (int(row.year), str(row.scope), str(row.side), str(row.split), int(row.state))
            for row in task_rows[["year", "scope", "side", "split", "state"]].itertuples(index=False)
            if not pd.isna(row.year) and not pd.isna(row.state)
        }
        missing_task = sorted(expected_task - seen_task)
        if missing_task:
            acc.fail("state_support_task_coverage_missing", "state_support.csv must include all task/side/year/split/state rows", missing=missing_task[:20], missing_count=len(missing_task))
        for (year, task, side, state), group in task_rows.groupby(["year", "scope", "side", "state"], dropna=False):
            cal = group[group["split"].eq("cal")]
            ev = group[group["split"].eq("eval")]
            if cal.empty or float(cal["days"].iloc[0]) < 10:
                acc.fail("state_task_cal_days", "each task/side/state needs >=10 calibration expiry days", layer="model", year=int(year), task=task, side=side, state=int(state))
            if ev.empty or float(ev["days"].iloc[0]) < 30:
                acc.fail("state_task_eval_days", "each task/side/state needs >=30 evaluation expiry days", layer="model", year=int(year), task=task, side=side, state=int(state))
    result["passed"] = not any(f["check"].startswith("state_") or f["check"] == "missing_columns" for f in acc.failures)
    return result


def _selected_hmm_candidate(model: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    metadata = model.get("metadata") or {}
    selected_seed = (metadata.get("selected") or {}).get("hmm_seed")
    candidates = metadata.get("candidates") or []
    for item in candidates:
        if item.get("seed") == selected_seed:
            return item, metadata
    return None, metadata


def check_state_models(acc: Acceptance, states: dict[int, dict[str, Any]], protocol: dict[str, Any]) -> dict[str, Any]:
    tol = float(((protocol.get("models") or {}).get("state_parameters") or {}).get("tol", 1e-3))
    result = {"years": sorted(states.keys()), "tol": tol, "passed": False}
    for year in YEARS:
        if year not in states:
            continue
        wrapped = states[year]
        model = wrapped.get("model") or wrapped
        hmm = model.get("hmm") or {}
        trans = np.asarray(hmm.get("transmat"), dtype=float)
        if trans.shape != (2, 2) or not np.isfinite(trans).all():
            acc.fail("state_hmm_transmat_invalid", "HMM transition matrix must be finite 2x2", layer="model", year=year)
        elif bool((trans <= 1e-6).any() or (trans >= 1 - 1e-6).any()):
            acc.fail("state_hmm_transmat_degenerate", "HMM transition probabilities must not be degenerate", layer="model", year=year, transmat=trans.tolist())

        candidate, metadata = _selected_hmm_candidate(model)
        if candidate is None:
            acc.fail("state_selected_candidate_missing", "selected HMM seed must match a retained candidate", layer="model", year=year)
            continue
        delta = candidate.get("hmm_final_delta")
        if delta is None or not np.isfinite(float(delta)) or float(delta) < -1e-6 or float(delta) >= tol:
            acc.fail(
                "state_selected_em_not_converged_by_delta",
                "selected HMM EM convergence must be proven by final delta, not monitor flag",
                layer="model",
                year=year,
                seed=candidate.get("seed"),
                hmm_final_delta=delta,
                tol=tol,
                monitor_converged=candidate.get("hmm_converged"),
                hit_iter_limit=candidate.get("hmm_hit_iter_limit"),
            )
        if metadata.get("gmm_total_initializations") is not None and int(metadata.get("gmm_total_initializations")) != 3:
            acc.fail("state_gmm_initialization_count", "protocol freezes exactly three GMM initializations", layer="model", year=year, value=metadata.get("gmm_total_initializations"))
        n_init = [item.get("gmm_n_init") for item in metadata.get("candidates", [])]
        if n_init and n_init != [1, 1, 1]:
            acc.fail("state_gmm_candidate_n_init", "each retained GMM candidate must use n_init=1", layer="model", year=year, values=n_init)
    result["passed"] = not any(f["check"].startswith("state_hmm_") or f["check"].startswith("state_selected_") or f["check"].startswith("state_gmm_") for f in acc.failures)
    return result


def check_scenario_gates(acc: Acceptance, contrasts: list[dict[str, Any]], protocol: dict[str, Any]) -> dict[str, Any]:
    primary = ((protocol.get("valuation") or {}).get("primary")) or "IV60"
    scenarios = [s["id"] for s in ((protocol.get("valuation") or {}).get("scenarios") or [])]
    rows = {(r.get("task"), r.get("scenario"), r.get("comparison")): r for r in contrasts}
    tasks = ["exit", "structure"]

    result: dict[str, Any] = {
        "primary_scenario": primary,
        "primary_comparisons": [f"{task}:HMM2-{comp}" for task in tasks for comp in PRIMARY_COMPARATORS],
        "tasks": {},
        "hmm_reset_diagnostic": [],
    }
    for task in tasks:
        task_result = {"qualified": True, "comparisons": {}}
        for comp in PRIMARY_COMPARATORS:
            key = (task, primary, f"HMM2-{comp}")
            row = rows.get(key)
            if row is None:
                acc.fail("scenario_primary_comparison_missing", "primary comparison must be HMM2 minus comparator", layer="research", task=task, scenario=primary, comparison=f"HMM2-{comp}")
                task_result["qualified"] = False
                continue
            problems = []
            if row.get("lower") is None or float(row.get("lower")) <= 0:
                problems.append("ci_lower")
                acc.fail("scenario_primary_ci_lower_not_positive", "primary IV60 CI lower bound must be >0", layer="research", task=task, comparison=f"HMM2-{comp}", lower=row.get("lower"))
            if int(row.get("nonworse_years", -1)) < 3:
                problems.append("annual_nonworse")
                acc.fail("scenario_primary_annual_nonworse", "primary IV60 comparison must be nonworse in >=3/4 years", layer="research", task=task, comparison=f"HMM2-{comp}", nonworse_years=row.get("nonworse_years"))
            if row.get("without10best") is None or float(row.get("without10best")) <= 0:
                problems.append("without10best")
                acc.fail("scenario_primary_without10best", "primary IV60 comparison must stay positive after removing ten best days", layer="research", task=task, comparison=f"HMM2-{comp}", without10best=row.get("without10best"))
            if row.get("es95_nonworse_both_sides") is not True:
                problems.append("es95")
                acc.fail("scenario_primary_es95", "primary IV60 comparison must be ES95 nonworse on both sides", layer="research", task=task, comparison=f"HMM2-{comp}", es95=row.get("es95_nonworse_both_sides"))
            task_result["comparisons"][f"HMM2-{comp}"] = {"qualified": not problems, "problems": problems}
            task_result["qualified"] = task_result["qualified"] and not problems
        for scenario in scenarios:
            if scenario == primary:
                continue
            for comp in PRIMARY_COMPARATORS:
                row = rows.get((task, scenario, f"HMM2-{comp}"))
                if row is None:
                    acc.fail("scenario_alternative_comparison_missing", "alternative scenario comparison is missing", layer="research", task=task, scenario=scenario, comparison=f"HMM2-{comp}")
                    task_result["qualified"] = False
                elif row.get("mean") is None or float(row.get("mean")) <= 0:
                    acc.fail("scenario_alternative_mean_not_positive", "all alternative scenario mean increments must be positive", layer="research", task=task, scenario=scenario, comparison=f"HMM2-{comp}", mean=row.get("mean"))
                    task_result["qualified"] = False
        reset_row = rows.get((task, primary, "HMM2-HMM_RESET"))
        if reset_row is not None:
            result["hmm_reset_diagnostic"].append({"task": task, "scenario": primary, "mean": reset_row.get("mean"), "lower": reset_row.get("lower")})
        result["tasks"][task] = task_result
    return result


def check_actual_ev_null(acc: Acceptance, scenario_metrics: pd.DataFrame) -> dict[str, Any]:
    result = {"actual_market_EV": None, "natural_nr_effect": None, "passed": True}
    actual_cols = [c for c in scenario_metrics.columns if c.lower() in {"actual_market_ev", "actual_ev"}]
    for column in actual_cols:
        if not scenario_metrics[column].isna().all():
            acc.fail("actual_ev_not_null", "actual market EV must remain null in synthetic scenario validation", column=column)
            result["passed"] = False
    return result


def _scenario_from_ledger_name(path: Path) -> tuple[str, str]:
    stem = path.stem
    marker = "_scenario_"
    if marker not in stem:
        raise ValueError(f"not a scenario ledger: {path.name}")
    task, scenario = stem.split(marker, 1)
    return task, scenario


def _metric_row(metrics: pd.DataFrame, task: str, scenario: str, side: str, method: str) -> pd.Series | None:
    part = metrics[
        metrics["task"].eq(task)
        & metrics["scenario"].eq(scenario)
        & metrics["side"].eq(side)
        & metrics["method"].eq(method)
    ]
    if len(part) != 1:
        return None
    return part.iloc[0]


def check_scenario_ledgers(acc: Acceptance, run_files: dict[str, Any]) -> dict[str, Any]:
    metrics = run_files["scenario_metrics"]
    result = {"ledgers": len(run_files["scenario_ledgers"]), "passed": False}
    if metrics.empty:
        acc.fail("scenario_metrics_empty", "scenario_metrics.csv is empty")
        return result
    required_metrics = ["task", "scenario", "side", "method", "gain_original_denominator", "unknown_original_weight", "mean_known_gain", "action_original_weight"]
    if not _require_columns(acc, metrics, "scenario_metrics.csv", required_metrics):
        return result
    for path in run_files["scenario_ledgers"]:
        frame = _read_csv(acc, path)
        if frame.empty:
            continue
        task, scenario = _scenario_from_ledger_name(path)
        required = ["row_id", "delivery_date", "side", "original_weight", "known_path", "target", "assumed_threshold"]
        if not _require_columns(acc, frame, path.name, required):
            continue
        methods = sorted(col[: -len("_action")] for col in frame.columns if col.endswith("_action"))
        if not methods:
            acc.fail("ledger_missing_actions", "scenario ledger has no action columns", path=path.name)
            continue
        known = _bool_series(frame["known_path"]).to_numpy(bool)
        target = pd.to_numeric(frame["target"], errors="coerce").to_numpy(float)
        threshold = pd.to_numeric(frame["assumed_threshold"], errors="coerce").to_numpy(float)
        if not np.isfinite(target[known]).all() or not np.isfinite(threshold[known]).all():
            acc.fail("ledger_invalid_target_threshold", "known ledger rows need finite target and threshold", path=path.name)

        base_sum: np.ndarray | None = None
        for method in methods:
            action = _bool_series(frame[f"{method}_action"]).to_numpy(bool)
            gain = pd.to_numeric(frame.get(f"{method}_gain"), errors="coerce").to_numpy(float)
            loss = pd.to_numeric(frame.get(f"{method}_loss"), errors="coerce").to_numpy(float)
            if (~known).any() and (not pd.isna(gain[~known]).all() or not pd.isna(loss[~known]).all()):
                acc.fail("ledger_unknown_not_nan", "unknown rows must keep gain/loss NaN, not zero-filled", path=path.name, method=method)
            expected_gain = np.where(action[known], target[known] - threshold[known], 0.0)
            if not np.allclose(gain[known], expected_gain, rtol=0, atol=1e-8, equal_nan=False):
                acc.fail("ledger_gain_identity", "known-row action/gain identity failed", path=path.name, method=method)
            current_sum = gain[known] + loss[known]
            if base_sum is None:
                base_sum = current_sum
            elif not np.allclose(current_sum, base_sum, rtol=0, atol=1e-8, equal_nan=False):
                acc.fail("ledger_loss_gain_identity", "loss + gain must be row-constant across policies", path=path.name, method=method)

            for side in sorted(frame["side"].dropna().unique()):
                side_mask = frame["side"].eq(side).to_numpy(bool)
                dates = int(frame.loc[side_mask, "delivery_date"].nunique())
                if dates <= 0:
                    continue
                known_side = side_mask & known
                weights = pd.to_numeric(frame.loc[known_side, "original_weight"], errors="coerce").to_numpy(float)
                gain_side = gain[known_side]
                action_side = action[known_side]
                metric = _metric_row(metrics, task, scenario, side, method)
                if metric is None:
                    acc.fail("scenario_metric_missing", "scenario metric row missing for ledger aggregation", task=task, scenario=scenario, side=side, method=method)
                    continue
                recomputed = {
                    "gain_original_denominator": float(np.sum(weights * gain_side) / dates) if len(weights) else 0.0,
                    "unknown_original_weight": float(pd.to_numeric(frame.loc[side_mask & ~known, "original_weight"], errors="coerce").sum() / dates),
                    "mean_known_gain": float(np.average(gain_side, weights=weights)) if len(weights) and float(weights.sum()) > 0 else np.nan,
                    "action_original_weight": float(np.sum(weights * action_side) / dates) if len(weights) else 0.0,
                }
                for column, value in recomputed.items():
                    observed = float(metric[column]) if not _is_null(metric[column]) else np.nan
                    if not np.isclose(observed, value, rtol=0, atol=1e-8, equal_nan=True):
                        acc.fail(
                            "scenario_metric_aggregate_mismatch",
                            "scenario metric aggregate does not match ledger original-weight recomputation",
                            task=task,
                            scenario=scenario,
                            side=side,
                            method=method,
                            column=column,
                            observed=observed,
                            recomputed=value,
                        )
    result["passed"] = not any(f["check"].startswith("ledger_") or f["check"].startswith("scenario_metric_") for f in acc.failures)
    return result


def validate_run(research: Path, run: str, output: str) -> dict[str, Any]:
    research = research.resolve()
    run_dir = research / run
    out_dir = research / output
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=False, exist_ok=False)

    acc = Acceptance()
    protocol_path = research / "protocol_v15.json"
    if not protocol_path.exists():
        acc.fail("missing_file", "protocol_v15.json is missing", path=str(protocol_path))
        protocol: dict[str, Any] = {}
    else:
        protocol = _read_json(protocol_path)
    if not run_dir.exists():
        acc.fail("missing_run", "run directory is missing", run=str(run_dir))
    elif (run_dir / "STOPPED_SOURCE_IDENTITY.json").exists():
        acc.fail(
            "stopped_source_identity",
            "run was explicitly stopped for source-identity defect and cannot pass engineering acceptance",
            marker=str(run_dir / "STOPPED_SOURCE_IDENTITY.json"),
        )
    run_files = _load_required(run_dir, acc) if run_dir.exists() else {
        "predictions": pd.DataFrame(),
        "state_support": pd.DataFrame(),
        "fit_log.json": [],
        "scenario_contrasts.json": [],
        "predictive_contrasts.json": [],
        "scenario_metrics": pd.DataFrame(),
        "predictive_metrics": pd.DataFrame(),
        "states": {},
        "scenario_ledgers": [],
    }

    engineering = {
        "source_identity": check_source_identity(acc, research, run_dir, protocol),
        "predictions": check_predictions(acc, run_files["predictions"]),
        "fit_log": check_fit_log(acc, run_files["fit_log.json"]),
        "state_support": check_state_support(acc, run_files["state_support"]),
        "state_models": check_state_models(acc, run_files["states"], protocol),
        "scenario_ledgers": check_scenario_ledgers(acc, run_files),
    }
    scenario_qualification = check_scenario_gates(acc, run_files["scenario_contrasts.json"], protocol)
    actual_ev = check_actual_ev_null(acc, run_files["scenario_metrics"])
    statistical_diagnostics = {
        "predictive_contrasts_file_read": isinstance(run_files["predictive_contrasts.json"], list),
        "predictive_metrics_rows": int(len(run_files["predictive_metrics"])),
        "used_for_acceptance_gate": False,
        "reason": "MSE diagnostics cannot replace the primary scenario gate",
    }

    engineering_passed = acc.layer_ok("engineering")
    model_passed = acc.layer_ok("model")
    research_qualified = engineering_passed and model_passed and acc.layer_ok("research")
    accepted = research_qualified
    local_acceptance = {
        "schema": "astra_state_acceptance@1.5.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research": str(research),
        "run": run,
        "accepted": accepted,
        "engineering_passed": engineering_passed,
        "model_qualification": {
            "qualified": model_passed,
            "state_support_passed": engineering["state_support"]["passed"],
            "state_model_passed": engineering["state_models"]["passed"],
            "head_calibration_passed": engineering["fit_log"]["passed"],
        },
        "research_qualified": research_qualified,
        "engineering_checks": engineering,
        "statistical_diagnostics": statistical_diagnostics,
        "scenario_qualification": scenario_qualification,
        "actual_EV": actual_ev,
        "production": {
            "runtime_model_changed": False,
            "fmz_changed": False,
            "forward_90d_started": False,
        },
        "failure_count": len(acc.failures),
        "failure_count_by_layer": _layer_count(acc.failures),
    }
    _write_json(out_dir / "local_acceptance.json", local_acceptance)
    _write_json(out_dir / "failure_ledger.json", {"schema": "astra_state_acceptance_failures@1.5.0", "failures": acc.failures})
    (out_dir / "summary.md").write_text(_summary_markdown(local_acceptance, acc.failures), encoding="utf-8")
    return local_acceptance


def _summary_markdown(local_acceptance: dict[str, Any], failures: list[dict[str, Any]]) -> str:
    status = "PASS" if local_acceptance["accepted"] else "FAIL"
    lines = [
        f"# Astra State v1.5 Acceptance: {status}",
        "",
        f"- Run: `{local_acceptance['run']}`",
        f"- Engineering passed: `{local_acceptance['engineering_passed']}`",
        f"- Model qualified: `{local_acceptance['model_qualification']['qualified']}`",
        f"- Research qualified: `{local_acceptance['research_qualified']}`",
        f"- Failure count: {local_acceptance['failure_count']}",
        "- Actual market EV: null; synthetic scenario validation only.",
        "- Predictive MSE diagnostics were read but not used as a substitute for the scenario gate.",
        "",
        "## Scenario Gate",
    ]
    for task, info in sorted(local_acceptance["scenario_qualification"].get("tasks", {}).items()):
        lines.append(f"- {task}: {'qualified' if info.get('qualified') else 'unqualified'}")
    if failures:
        lines.extend(["", "## Failures"])
        for failure in failures[:50]:
            lines.append(f"- `{failure['check']}`: {failure['message']} {json.dumps(failure.get('context', {}), ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    validate_run(args.research, args.run, args.output)


if __name__ == "__main__":
    main()
