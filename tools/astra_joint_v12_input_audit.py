"""Recheck sealed input identities, timing, and inverse settlement without rewriting data."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from astra_joint_v12_diagnostics import sha256


def audit(research):
    anomaly_path = research / "semantic_audit/anomalies.jsonl"
    anomalies = [json.loads(line) for line in anomaly_path.read_text(encoding="utf-8").splitlines()]
    anomaly_times = {int(item["row"][6]) for item in anomalies}
    issues, hits, files = [], [], {}
    rows = primary = over_one = 0
    maximum_loss = 0.
    for path in sorted((research / "step30/model_input").glob("*.csv")):
        files[path.name] = sha256(path)
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                rows += 1
                primary += float(row["target_width"]) == 2000
                at, price_time, expiry = map(int, (row["as_of_ms"], row["price_observation_ms"], row["expiry_ms"]))
                spot, settlement, short, long, width, loss = map(float, (row["entry_price"], row["settlement_price"], row["short_strike"], row["long_strike"], row["actual_width"], row["loss_normalized"]))
                maximum_loss, over_one = max(maximum_loss, loss), over_one + (loss > 1)
                if price_time in anomaly_times:
                    hits.append(row["row_id"])
                failures = []
                if not price_time < at:
                    failures.append("reference_not_prior")
                if max(int(row["short_creation_ms"]), int(row["long_creation_ms"])) > at:
                    failures.append("leg_created_later")
                if not 8 < (expiry - at) / 3600000 <= 24:
                    failures.append("dte")
                if abs(abs(short - long) - width) > 1e-10:
                    failures.append("width")
                if row["side"] not in ("put", "call"):
                    failures.append("side")
                intrinsic = min(max(short - settlement if row["side"] == "put" else settlement - short, 0), width)
                if abs(intrinsic / settlement / (width / spot) - loss) > 1e-10:
                    failures.append("coin_payout_formula")
                if row["protection_leg_breached"] == "true" and loss <= 0:
                    failures.append("label_inclusion")
                if failures:
                    issues.append({"row_id": row["row_id"], "issues": failures})
    observation_hits = []
    observation_path = research / "step30/decisions/market_observations.jsonl"
    with observation_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("price_observation_ms") in anomaly_times:
                observation_hits.append({key: row.get(key) for key in ("observation_id", "as_of_ms", "price_observation_ms", "spot_open_time_ms")})
    return {
        "schema": "astra_joint_v12_input_recheck@1.0.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256": files, "observation_sha256": sha256(observation_path), "anomalies_sha256": sha256(anomaly_path),
        "rows_checked": rows, "primary_width_rows": primary, "model_input_anomaly_hits": hits,
        "archived_observation_anomaly_hits": observation_hits, "integrity_issues": issues,
        "realized_loss_above_one_rows": over_one, "maximum_realized_loss": maximum_loss,
        "receipt_time": "historical closed-bar replay only; original receipt latency and original API archive vintage unproven",
        "reproduction_limit": "Current reference_spot_close guard postdates archived observations; no bitwise historical observation regeneration claim. Sealed model rows reused by hash; archives unmodified.",
        "training_permitted_on_frozen_rows": not issues and not hits,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit(args.research)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("rows_checked", "primary_width_rows", "model_input_anomaly_hits", "integrity_issues", "training_permitted_on_frozen_rows")}, ensure_ascii=False))
