"""Verify archived conditional-tail predictions using only the standard library.

Run with Python -S. No fitting, network calls, or artifact overwrites.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

from astra_joint_v12_inference import predict_nested_tail


def check(research: Path, experiment: Path, output: Path):
    if output.exists():
        raise ValueError("output already exists; preserve prior verification")
    summary = json.loads((experiment / "v12_summary.json").read_text(encoding="utf-8"))
    models = {x["fold"]: x["model"] for x in summary["tail"]["fold_support"]}
    with (experiment / "v12_row_predictions.csv").open(encoding="utf-8", newline="") as handle:
        predictions = {x["row_id"]: x for x in csv.DictReader(handle)
                       if x["candidate_id"] == "NESTED_LOGIT_C01"}
    checked, maximum, mismatches = 0, 0.0, 0
    seen = set()
    for year in range(2022, 2026):
        with (research / "step30/model_input" / f"model_rows-{year}.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                expected = predictions.get(row["row_id"])
                if expected is None:
                    continue
                if row["row_id"] in seen:
                    raise ValueError("duplicate validation row")
                seen.add(row["row_id"])
                row["parent_probability_positive"] = expected["parent_probability_positive"]
                result = predict_nested_tail(row, models[expected["fold"]])
                if result["status"] != "available":
                    raise ValueError(result)
                diff = abs(result["tail_probability"] - float(expected["tail_probability"]))
                maximum = max(maximum, diff)
                mismatches += int(diff > 1e-12)
                checked += 1
    training_imports = sorted(set(sys.modules) & {"numpy", "pandas", "sklearn", "catboost", "scipy"})
    if checked != len(predictions) or not checked or mismatches or training_imports:
        raise ValueError((checked, len(predictions), mismatches, training_imports))
    report = dict(schema="astra_joint_v12_stdlib_prediction_check@1.0.0",
                  platform=sys.platform, no_site=sys.flags.no_site,
                  rows_checked=checked, max_abs_error=maximum,
                  tolerance=1e-12, mismatches=mismatches, training_imports=training_imports,
                  summary_sha256=hashlib.sha256((experiment / "v12_summary.json").read_bytes()).hexdigest(),
                  scope="archived four-fold conditional-tail coefficients and row predictions only; not qualification or Linux acceptance")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.research, args.experiment, args.output), ensure_ascii=False, indent=2))
