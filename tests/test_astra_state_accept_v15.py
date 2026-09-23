import json
import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from astra_state_accept_v15 import validate_run


YEARS = (2022, 2023, 2024, 2025)
METHODS = ("WINDOWS", "DYNAMIC", "MIX2", "HMM2", "HMM_RESET")
SCENARIOS = ("IV40", "IV60", "IV80", "IV60_COST5")


def ms(date):
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build_fixture(root: Path):
    research = root / "R15"
    run = research / "run_01"
    run.mkdir(parents=True)
    write_json(
        research / "protocol_v15.json",
        {
            "models": {"state_parameters": {"tol": 0.001}},
            "valuation": {
                "primary": "IV60",
                "scenarios": [
                    {"id": "IV40"},
                    {"id": "IV60"},
                    {"id": "IV80"},
                    {"id": "IV60_COST5"},
                ],
            },
        },
    )
    write_json(research / "source_manifest.json", {"files": {}})
    write_json(
        research / "protocol_seal.json",
        {
            "protocol_sha256": sha(research / "protocol_v15.json"),
            "source_manifest_sha256": sha(research / "source_manifest.json"),
        },
    )
    source_files = {
        "tools/astra_state_audit_v15.py": "a" * 64,
        "tools/astra_state_v15.py": "b" * 64,
        "tools/astra_state_accept_v15.py": "c" * 64,
    }
    write_json(
        research / "source_correction_02.json",
        {
            "parent_protocol_sha256": json.loads((research / "protocol_seal.json").read_text(encoding="utf-8"))["protocol_sha256"],
            "source_files": source_files,
            "output": "run_01",
        },
    )
    write_json(research / "source_correction_02_seal.json", {"sha256": sha(research / "source_correction_02.json")})
    write_json(
        run / "execution_identity.json",
        {
            "protocol_sha256": json.loads((research / "protocol_seal.json").read_text(encoding="utf-8"))["protocol_sha256"],
            "source_correction_sha256": json.loads((research / "source_correction_02_seal.json").read_text(encoding="utf-8"))["sha256"],
            "source_code": {
                "C:/repo/tools/astra_state_audit_v15.py": source_files["tools/astra_state_audit_v15.py"],
                "C:/repo/tools/astra_state_v15.py": source_files["tools/astra_state_v15.py"],
            },
        },
    )
    predictions = []
    for i, task in enumerate(("exit", "structure")):
        for year in YEARS:
            predictions.append(
                {
                    "task": task,
                    "row_id": f"{task}-{year}",
                    "side": "put" if i == 0 else "call",
                    "entry_ms": ms(f"{year}-02-01"),
                    "expiry_ms": ms(f"{year}-03-01"),
                    "delivery_date": f"{year}-03-01",
                    "target": 0.4,
                    "year": year,
                    "hmm_p1": 0.55,
                    **{method: 0.2 for method in METHODS},
                }
            )
    pd.DataFrame(predictions).to_csv(run / "predictions.csv", index=False)

    fit_log = []
    for task in ("exit", "structure"):
        for side in ("put", "call"):
            for year in YEARS:
                for method in METHODS:
                    fit_log.append(
                        {
                            "task": task,
                            "side": side,
                            "year": year,
                            "method": method,
                            "fit_max_expiry": ms(f"{year - 1}-09-15"),
                            "cal_start": ms(f"{year - 1}-10-01"),
                            "cal_max_expiry": ms(f"{year - 1}-12-15"),
                            "eval_start": ms(f"{year}-01-01"),
                            "calibration": {"days": 30, "qualified": True},
                        }
                    )
    write_json(run / "fit_log.json", fit_log)

    support = []
    for year in YEARS:
        for split in ("fit", "cal", "eval"):
            for state in (0, 1):
                support.append({"year": year, "scope": "market", "split": split, "state": state, "fraction": 0.5, "days": 31, "side": ""})
        for task in ("exit", "structure"):
            for side in ("put", "call"):
                for split in ("fit", "cal", "eval"):
                    for state in (0, 1):
                        support.append(
                            {
                                "year": year,
                                "scope": task,
                                "side": side,
                                "split": split,
                                "state": state,
                                "fraction": 0.5,
                                "days": 10 if split == "cal" else 30,
                            }
                        )
    pd.DataFrame(support).to_csv(run / "state_support.csv", index=False)

    for year in YEARS:
        write_json(
            run / f"states_{year}.json",
            {
                "model": {
                    "hmm": {"transmat": [[0.85, 0.15], [0.2, 0.8]]},
                    "metadata": {
                        "gmm_total_initializations": 3,
                        "selected": {"hmm_seed": 20260922},
                        "candidates": [
                            {"seed": 20260922, "hmm_final_delta": 0.0002, "hmm_converged": True, "hmm_hit_iter_limit": False, "gmm_n_init": 1},
                            {"seed": 20260923, "hmm_final_delta": 0.0003, "hmm_converged": True, "hmm_hit_iter_limit": False, "gmm_n_init": 1},
                            {"seed": 20260924, "hmm_final_delta": 0.0004, "hmm_converged": True, "hmm_hit_iter_limit": False, "gmm_n_init": 1},
                        ],
                    },
                }
            },
        )

    scenario_contrasts = []
    for task in ("exit", "structure"):
        for scenario in SCENARIOS:
            for comparison in ("HMM2-DYNAMIC", "HMM2-MIX2", "HMM2-HMM_RESET"):
                scenario_contrasts.append(
                    {
                        "task": task,
                        "scenario": scenario,
                        "comparison": comparison,
                        "mean": 0.2,
                        "lower": 0.05,
                        "without10best": 0.1,
                        "nonworse_years": 4,
                        "es95_nonworse_both_sides": True,
                    }
                )
    write_json(run / "scenario_contrasts.json", scenario_contrasts)
    write_json(run / "predictive_contrasts.json", [{"task": "exit", "comparison": "HMM2-DYNAMIC", "upper": -0.1}])
    pd.DataFrame([{"task": "exit", "side": "put", "method": "HMM2", "mse": 0.1, "bias": 0.0}]).to_csv(run / "predictive_metrics.csv", index=False)

    metrics = []
    for task in ("exit", "structure"):
        controls = ("HOLD", "EXIT_ALL_TOUCH") if task == "exit" else ("ORIGINAL", "OUTWARD_ALL")
        for scenario in SCENARIOS:
            ledger = []
            for side in ("put", "call"):
                row = {
                    "row_id": f"{task}-{scenario}-{side}",
                    "delivery_date": "2022-03-01" if side == "put" else "2022-03-02",
                    "side": side,
                    "original_weight": 1.0,
                    "known_path": True,
                    "prediction_available": True,
                    "target": 1.0,
                    "assumed_threshold": 0.25,
                }
                for method in (*controls, *METHODS):
                    action = method in {"HMM2", "EXIT_ALL_TOUCH", "OUTWARD_ALL"}
                    gain = 0.75 if action else 0.0
                    row[f"{method}_action"] = action
                    row[f"{method}_gain"] = gain
                    row[f"{method}_loss"] = 1.0 - gain
                    metrics.append(
                        {
                            "task": task,
                            "scenario": scenario,
                            "side": side,
                            "method": method,
                            "gain_original_denominator": gain,
                            "unknown_original_weight": 0.0,
                            "mean_known_gain": gain,
                            "action_original_weight": 1.0 if action else 0.0,
                            "actual_market_ev": np.nan,
                        }
                    )
                ledger.append(row)
            pd.DataFrame(ledger).to_csv(run / f"{task}_scenario_{scenario}.csv", index=False)
    pd.DataFrame(metrics).to_csv(run / "scenario_metrics.csv", index=False)
    return research


def test_acceptance_passes_clean_synthetic_fixture(tmp_path):
    research = build_fixture(tmp_path)
    result = validate_run(research, "run_01", "validation_01")
    assert result["accepted"] is True
    assert result["engineering_passed"] is True
    assert result["model_qualification"]["qualified"] is True
    assert result["research_qualified"] is True
    assert result["statistical_diagnostics"]["used_for_acceptance_gate"] is False
    assert result["actual_EV"]["actual_market_EV"] is None
    assert (research / "validation_01" / "local_acceptance.json").exists()


def test_primary_scenario_failure_is_not_rescued_by_predictive_mse(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "scenario_contrasts.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if row["task"] == "exit" and row["scenario"] == "IV60" and row["comparison"] == "HMM2-DYNAMIC":
            row["lower"] = -0.01
    write_json(path, rows)

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is True
    assert result["model_qualification"]["qualified"] is True
    assert result["research_qualified"] is False
    assert any(f["check"] == "scenario_primary_ci_lower_not_positive" for f in failures)
    assert any(f["check"] == "scenario_primary_ci_lower_not_positive" and f["layer"] == "research" for f in failures)
    assert result["statistical_diagnostics"]["used_for_acceptance_gate"] is False


def test_reverse_comparison_name_does_not_satisfy_hmm_minus_comparator_gate(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "scenario_contrasts.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in rows if not (row["task"] == "structure" and row["scenario"] == "IV60" and row["comparison"] == "HMM2-MIX2")]
    rows.append(
        {
            "task": "structure",
            "scenario": "IV60",
            "comparison": "MIX2-HMM2",
            "mean": 99.0,
            "lower": 99.0,
            "without10best": 99.0,
            "nonworse_years": 4,
            "es95_nonworse_both_sides": True,
        }
    )
    write_json(path, rows)

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is True
    assert any(
        f["check"] == "scenario_primary_comparison_missing"
        and f["layer"] == "research"
        and f["context"]["task"] == "structure"
        and f["context"]["comparison"] == "HMM2-MIX2"
        for f in failures
    )


def test_extra_diagnostic_task_is_not_primary_gate(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "scenario_contrasts.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.append(
        {
            "task": "diagnostic_only",
            "scenario": "IV60",
            "comparison": "HMM2-DYNAMIC",
            "mean": -99.0,
            "lower": -99.0,
            "without10best": -99.0,
            "nonworse_years": 0,
            "es95_nonworse_both_sides": False,
        }
    )
    write_json(path, rows)

    result = validate_run(research, "run_01", "validation_01")
    assert result["accepted"] is True
    assert result["engineering_passed"] is True
    assert result["scenario_qualification"]["primary_comparisons"] == [
        "exit:HMM2-DYNAMIC",
        "exit:HMM2-MIX2",
        "structure:HMM2-DYNAMIC",
        "structure:HMM2-MIX2",
    ]


def test_stopped_source_identity_marker_blocks_engineering_acceptance(tmp_path):
    research = build_fixture(tmp_path)
    write_json(research / "run_01" / "STOPPED_SOURCE_IDENTITY.json", {"status": "STOPPED_SOURCE_IDENTITY"})

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is False
    assert any(f["check"] == "stopped_source_identity" for f in failures)


def test_model_calibration_failure_does_not_make_engineering_fail(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "fit_log.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["calibration"] = {"days": 9, "qualified": False}
    write_json(path, rows)

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is True
    assert result["model_qualification"]["qualified"] is False
    assert result["research_qualified"] is False
    assert any(f["check"] == "head_calibration_unqualified" and f["layer"] == "model" for f in failures)


def test_missing_state_support_task_row_is_engineering_incomplete(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "state_support.csv"
    frame = pd.read_csv(path)
    frame = frame.drop(frame[(frame.scope == "exit") & (frame.side == "put") & (frame.year == 2022) & (frame.split == "cal") & (frame.state == 0)].index[:1])
    frame.to_csv(path, index=False)

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is False
    assert any(f["check"] == "state_support_task_coverage_missing" and f["layer"] == "engineering" for f in failures)


def test_unknown_ledger_rows_must_remain_nan_not_zero_filled(tmp_path):
    research = build_fixture(tmp_path)
    path = research / "run_01" / "exit_scenario_IV60.csv"
    frame = pd.read_csv(path)
    frame.loc[0, "known_path"] = False
    frame.loc[0, "HMM2_gain"] = 0.0
    frame.loc[0, "HMM2_loss"] = 0.0
    frame.to_csv(path, index=False)

    result = validate_run(research, "run_01", "validation_01")
    failures = json.loads((research / "validation_01" / "failure_ledger.json").read_text(encoding="utf-8"))["failures"]
    assert result["accepted"] is False
    assert result["engineering_passed"] is False
    assert any(f["check"] == "ledger_unknown_not_nan" for f in failures)


def test_output_directory_must_not_already_exist(tmp_path):
    research = build_fixture(tmp_path)
    (research / "validation_01").mkdir()
    with pytest.raises(FileExistsError):
        validate_run(research, "run_01", "validation_01")
