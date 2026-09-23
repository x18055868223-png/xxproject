from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MINUTE_MS = 60_000
DEFAULT_REQUIRED_DAYS = 28
DEFAULT_TARGET_WIDTH = 2000.0
DEFAULT_CREDIT_FRACTION = 0.10
DEFAULT_MARGIN_LAMBDA = 0.9516590272
EPSILON = 1e-12


def finite_float(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def int_ms(value: object) -> int:
    parsed = finite_float(value)
    if parsed is None:
        raise ValueError(f"missing millisecond timestamp: {value!r}")
    return int(parsed)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    materialized = list(rows)
    if fieldnames is None:
        fieldnames = []
        seen = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def save_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def option_intrinsic_usd(side: str, short_strike: float, long_strike: float, settlement: float) -> float:
    if settlement <= 0:
        raise ValueError("settlement must be positive")
    if side == "put_credit":
        if not long_strike < short_strike:
            raise ValueError("put legs must be ordered long < short")
        return max(short_strike - settlement, 0.0) - max(long_strike - settlement, 0.0)
    if side == "call_credit":
        if not short_strike < long_strike:
            raise ValueError("call legs must be ordered short < long")
        return max(settlement - short_strike, 0.0) - max(settlement - long_strike, 0.0)
    raise ValueError(f"unknown side: {side}")


def inverse_payout_btc(side: str, short_strike: float, long_strike: float, settlement: float) -> float:
    return option_intrinsic_usd(side, short_strike, long_strike, settlement) / settlement


def margin_btc(entry_price: float, width: float, margin_lambda: float = DEFAULT_MARGIN_LAMBDA) -> float:
    if entry_price <= 0 or width <= 0 or margin_lambda <= 0:
        raise ValueError("entry price, width and margin lambda must be positive")
    return margin_lambda * width / entry_price


def project_current_trade(row: dict[str, object], asof_ms: int | None = None) -> dict[str, object]:
    side = str(row["side"])
    entry_price = finite_float(row.get("entry_price"))
    short_strike = finite_float(row.get("short_strike"))
    long_strike = finite_float(row.get("long_strike"))
    actual_width = finite_float(row.get("actual_width"))
    credit = finite_float(row.get("net_credit_btc"))
    dte_hours = finite_float(row.get("dte_hours"))
    if None in (entry_price, short_strike, long_strike, actual_width, credit, dte_hours):
        raise ValueError("current trade projection is missing required numeric fields")
    projected = {
        "card_id": row.get("card_id"),
        "episode_id": row.get("episode_id"),
        "version": row.get("version"),
        "direction": row.get("direction"),
        "side": side,
        "relation": row.get("relation"),
        "entry_ms": int_ms(row.get("entry_ms")),
        "asof_ms": asof_ms,
        "entry_price": entry_price,
        "expiry_ms": int_ms(row.get("expiry_ms")),
        "delivery_date": row.get("delivery_date"),
        "dte_hours": dte_hours,
        "short_instrument": row.get("short_instrument"),
        "long_instrument": row.get("long_instrument"),
        "short_strike": short_strike,
        "long_strike": long_strike,
        "target_width": finite_float(row.get("target_width")),
        "actual_width": actual_width,
        "credit_fraction": finite_float(row.get("credit_fraction")),
        "net_credit_btc": credit,
        "entry_beijing": row.get("entry_beijing"),
        "expiry_beijing": row.get("expiry_beijing"),
    }
    if projected["asof_ms"] is None:
        projected["asof_ms"] = projected["entry_ms"] - MINUTE_MS
    return projected


def load_current_candidates(
    second_report: Path,
    samples_path: Path,
    *,
    expected_cards: int | None = 114,
    expected_rows: int | None = 228,
) -> list[dict[str, object]]:
    sample_asof: dict[str, int] = {}
    for line in samples_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        sample_asof[str(sample["card_id"])] = int_ms(sample["event_time_ms"])

    projections: list[dict[str, object]] = []
    for row in read_csv(second_report):
        if finite_float(row.get("target_width")) != DEFAULT_TARGET_WIDTH:
            continue
        if finite_float(row.get("credit_fraction")) != DEFAULT_CREDIT_FRACTION:
            continue
        side = row.get("side")
        card_id = str(row.get("card_id"))
        if side not in {"put_credit", "call_credit"}:
            continue
        if str(row.get("cohort")) != "ordinary":
            continue
        projection = project_current_trade(row, sample_asof.get(card_id))
        projections.append(projection)

    cards = {str(row["card_id"]) for row in projections}
    if expected_cards is not None and expected_rows is not None and (len(cards) != expected_cards or len(projections) != expected_rows):
        raise ValueError(f"expected 114 cards and 228 current side rows, got {len(cards)} and {len(projections)}")
    return projections


def load_actual_outcomes(
    second_report: Path,
    *,
    expected_cards: int | None = 114,
    expected_rows: int | None = 228,
) -> dict[tuple[str, str], dict[str, str]]:
    actual: dict[tuple[str, str], dict[str, str]] = {}
    for row in read_csv(second_report):
        if finite_float(row.get("target_width")) != DEFAULT_TARGET_WIDTH:
            continue
        if finite_float(row.get("credit_fraction")) != DEFAULT_CREDIT_FRACTION:
            continue
        side = row.get("side")
        card_id = str(row.get("card_id"))
        if side not in {"put_credit", "call_credit"}:
            continue
        if str(row.get("cohort")) != "ordinary":
            continue
        actual[(card_id, str(side))] = {
            "result": row.get("result"),
            "net_pnl_btc": row.get("net_pnl_btc"),
            "holding_return_on_margin_1x": row.get("holding_return_on_margin_1x"),
        }

    cards = {card_id for card_id, _ in actual}
    if expected_cards is not None and expected_rows is not None and (len(cards) != expected_cards or len(actual) != expected_rows):
        raise ValueError(f"expected 114 cards and 228 actual side rows, got {len(cards)} and {len(actual)}")
    return actual


def load_clock_history(control_main: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_entries = set()
    for raw in read_csv(control_main):
        if str(raw.get("grid_offset_minutes")) != "0":
            continue
        if str(raw.get("side")) != "put_credit":
            continue
        if finite_float(raw.get("target_width")) != DEFAULT_TARGET_WIDTH:
            continue
        if finite_float(raw.get("credit_fraction")) != DEFAULT_CREDIT_FRACTION:
            continue
        if str(raw.get("is_matured")).lower() != "true":
            continue
        key = (int_ms(raw["entry_ms"]), int_ms(raw["expiry_ms"]))
        if key in seen_entries:
            continue
        seen_entries.add(key)
        entry_price = finite_float(raw.get("entry_price"))
        settlement = finite_float(raw.get("delivery_price"))
        if entry_price is None or settlement is None or entry_price <= 0:
            continue
        rows.append(
            {
                "clock_id": raw.get("card_id"),
                "entry_ms": int_ms(raw["entry_ms"]),
                "expiry_ms": int_ms(raw["expiry_ms"]),
                "delivery_date": raw.get("delivery_date"),
                "dte_hours": finite_float(raw.get("dte_hours")),
                "entry_price": entry_price,
                "delivery_price": settlement,
                "settlement_entry_ratio": settlement / entry_price,
                "entry_beijing": raw.get("entry_beijing"),
                "expiry_beijing": raw.get("expiry_beijing"),
            }
        )
    if not rows:
        raise ValueError("no offset-0 clock history rows available")
    return rows


def select_training_rows(
    history: Iterable[dict[str, object]],
    *,
    current_asof_ms: int,
    target_dte_hours: float,
    required_days: int = DEFAULT_REQUIRED_DAYS,
) -> tuple[list[dict[str, object]], int]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in history:
        expiry_ms = int(row["expiry_ms"])
        if expiry_ms + 5 * MINUTE_MS > current_asof_ms:
            continue
        groups[str(row["delivery_date"])].append(row)

    def group_expiry(item: tuple[str, list[dict[str, object]]]) -> int:
        return max(int(row["expiry_ms"]) for row in item[1])

    available_groups = sorted(groups.items(), key=group_expiry, reverse=True)
    selected_groups = available_groups[:required_days]
    selected: list[dict[str, object]] = []
    for _, rows in selected_groups:
        selected.append(
            min(
                rows,
                key=lambda row: (
                    abs(float(row["dte_hours"]) - target_dte_hours),
                    int(row["entry_ms"]),
                ),
            )
        )
    selected.sort(key=lambda row: int(row["expiry_ms"]))
    return selected, len(available_groups)


def simulate_with_ratio(current: dict[str, object], historical: dict[str, object], margin_lambda: float) -> dict[str, object]:
    entry_price = float(current["entry_price"])
    projected_settlement = entry_price * float(historical["settlement_entry_ratio"])
    payout = inverse_payout_btc(
        str(current["side"]),
        float(current["short_strike"]),
        float(current["long_strike"]),
        projected_settlement,
    )
    credit = float(current["net_credit_btc"])
    pnl = credit - payout
    margin = margin_btc(entry_price, float(current["actual_width"]), margin_lambda)
    return {
        "card_id": current["card_id"],
        "side": current["side"],
        "asof_ms": current["asof_ms"],
        "historical_clock_id": historical["clock_id"],
        "historical_delivery_date": historical["delivery_date"],
        "historical_entry_ms": historical["entry_ms"],
        "historical_expiry_ms": historical["expiry_ms"],
        "historical_dte_hours": historical["dte_hours"],
        "historical_entry_price": historical["entry_price"],
        "historical_delivery_price": historical["delivery_price"],
        "settlement_entry_ratio": historical["settlement_entry_ratio"],
        "projected_settlement": projected_settlement,
        "payout_btc": payout,
        "net_pnl_btc": pnl,
        "margin_btc": margin,
        "margin_return": pnl / margin,
        "result": "win" if pnl > EPSILON else "loss" if pnl < -EPSILON else "tie",
    }


def forecast_one(
    current: dict[str, object],
    history: Iterable[dict[str, object]],
    *,
    required_days: int = DEFAULT_REQUIRED_DAYS,
    margin_lambda: float = DEFAULT_MARGIN_LAMBDA,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    training, available_days = select_training_rows(
        history,
        current_asof_ms=int(current["asof_ms"]),
        target_dte_hours=float(current["dte_hours"]),
        required_days=required_days,
    )
    base = {
        "card_id": current["card_id"],
        "episode_id": current["episode_id"],
        "version": current["version"],
        "direction": current["direction"],
        "side": current["side"],
        "relation": current["relation"],
        "asof_ms": current["asof_ms"],
        "entry_ms": current["entry_ms"],
        "entry_price": current["entry_price"],
        "expiry_ms": current["expiry_ms"],
        "delivery_date": current["delivery_date"],
        "dte_hours": current["dte_hours"],
        "short_instrument": current["short_instrument"],
        "long_instrument": current["long_instrument"],
        "short_strike": current["short_strike"],
        "long_strike": current["long_strike"],
        "actual_width": current["actual_width"],
        "credit_fraction": current["credit_fraction"],
        "net_credit_btc": current["net_credit_btc"],
        "estimated_margin_btc": margin_btc(float(current["entry_price"]), float(current["actual_width"]), margin_lambda),
        "required_training_days": required_days,
        "available_completed_delivery_days": available_days,
        "training_days": len(training),
        "projection_hash": stable_hash(current),
    }
    if len(training) < required_days:
        prediction = {
            **base,
            "forecast_status": "insufficient_training_days",
            "gap_reason": f"requires {required_days} completed delivery dates",
            "training_start_delivery_date": None,
            "training_end_delivery_date": None,
            "empirical_win_probability": None,
            "empirical_loss_probability": None,
            "empirical_tie_probability": None,
            "empirical_mean_net_pnl_btc": None,
            "empirical_mean_margin_return": None,
            "empirical_worst_margin_return": None,
            "empirical_tail_p5_margin_return": None,
            "training_identity_hash": stable_hash(training),
        }
        return prediction, []

    paths = [simulate_with_ratio(current, row, margin_lambda) for row in training]
    wins = sum(1 for row in paths if row["result"] == "win")
    losses = sum(1 for row in paths if row["result"] == "loss")
    ties = len(paths) - wins - losses
    mean_pnl = sum(float(row["net_pnl_btc"]) for row in paths) / len(paths)
    returns = sorted(float(row["margin_return"]) for row in paths)
    p5_index = max(0, math.ceil(len(returns) * 0.05) - 1)
    prediction = {
        **base,
        "forecast_status": "available",
        "gap_reason": None,
        "training_start_delivery_date": training[0]["delivery_date"],
        "training_end_delivery_date": training[-1]["delivery_date"],
        "empirical_win_probability": wins / len(paths),
        "empirical_loss_probability": losses / len(paths),
        "empirical_tie_probability": ties / len(paths),
        "empirical_mean_net_pnl_btc": mean_pnl,
        "empirical_mean_margin_return": sum(returns) / len(returns),
        "empirical_worst_margin_return": returns[0],
        "empirical_tail_p5_margin_return": returns[p5_index],
        "training_identity_hash": stable_hash(
            [
                {
                    "clock_id": row["clock_id"],
                    "delivery_date": row["delivery_date"],
                    "entry_ms": row["entry_ms"],
                    "expiry_ms": row["expiry_ms"],
                    "dte_hours": row["dte_hours"],
                    "settlement_entry_ratio": row["settlement_entry_ratio"],
                }
                for row in training
            ]
        ),
    }
    return prediction, paths


def choose_side(
    put_prediction: dict[str, object] | None,
    call_prediction: dict[str, object] | None,
    metric: str,
) -> tuple[str, str, float | None]:
    if not put_prediction or not call_prediction:
        return "no_preference", "missing_side", None
    if put_prediction.get("forecast_status") != "available" or call_prediction.get("forecast_status") != "available":
        return "no_preference", "forecast_unavailable", None
    put_value = finite_float(put_prediction.get(metric))
    call_value = finite_float(call_prediction.get(metric))
    if put_value is None or call_value is None:
        return "no_preference", "metric_unavailable", None
    delta = put_value - call_value
    if abs(delta) <= EPSILON:
        return "no_preference", "tie", delta
    return ("put_credit", "higher_put", delta) if delta > 0 else ("call_credit", "higher_call", delta)


def build_side_choices(predictions: list[dict[str, object]]) -> list[dict[str, object]]:
    by_card: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for prediction in predictions:
        by_card[str(prediction["card_id"])][str(prediction["side"])] = prediction

    rows: list[dict[str, object]] = []
    for card_id in sorted(by_card):
        sides = by_card[card_id]
        put = sides.get("put_credit")
        call = sides.get("call_credit")
        prob_side, prob_reason, prob_delta = choose_side(put, call, "empirical_win_probability")
        ret_side, ret_reason, ret_delta = choose_side(put, call, "empirical_mean_margin_return")
        row = {
            "card_id": card_id,
            "direction": (put or call or {}).get("direction"),
            "asof_ms": (put or call or {}).get("asof_ms"),
            "entry_ms": (put or call or {}).get("entry_ms"),
            "put_forecast_status": put.get("forecast_status") if put else "missing",
            "call_forecast_status": call.get("forecast_status") if call else "missing",
            "put_empirical_win_probability": put.get("empirical_win_probability") if put else None,
            "call_empirical_win_probability": call.get("empirical_win_probability") if call else None,
            "put_empirical_mean_margin_return": put.get("empirical_mean_margin_return") if put else None,
            "call_empirical_mean_margin_return": call.get("empirical_mean_margin_return") if call else None,
            "probability_rule_side": prob_side,
            "probability_rule_reason": prob_reason,
            "probability_rule_delta": prob_delta,
            "margin_return_rule_side": ret_side,
            "margin_return_rule_reason": ret_reason,
            "margin_return_rule_delta": ret_delta,
        }
        rows.append(row)
    return rows


def attach_actual_results(
    choices: list[dict[str, object]],
    actual: dict[tuple[str, str], dict[str, str]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows: list[dict[str, object]] = []
    summaries = {}
    for choice in choices:
        card_id = str(choice["card_id"])
        row = dict(choice)
        for side in ("put_credit", "call_credit"):
            source = actual.get((card_id, side))
            prefix = "put" if side == "put_credit" else "call"
            row[f"{prefix}_actual_result"] = source.get("result") if source else None
            row[f"{prefix}_actual_net_pnl_btc"] = source.get("net_pnl_btc") if source else None
            row[f"{prefix}_actual_margin_return"] = source.get("holding_return_on_margin_1x") if source else None
        rows.append(row)

    for rule, column in (
        ("higher_empirical_net_profit_probability", "probability_rule_side"),
        ("higher_empirical_margin_return", "margin_return_rule_side"),
    ):
        selected = []
        for row in rows:
            side = row[column]
            if side not in {"put_credit", "call_credit"}:
                continue
            prefix = "put" if side == "put_credit" else "call"
            pnl = finite_float(row.get(f"{prefix}_actual_net_pnl_btc"))
            margin_return = finite_float(row.get(f"{prefix}_actual_margin_return"))
            if pnl is None or margin_return is None:
                continue
            selected.append((pnl, margin_return, row.get(f"{prefix}_actual_result")))
        wins = sum(1 for pnl, _, _ in selected if pnl > EPSILON)
        losses = sum(1 for pnl, _, _ in selected if pnl < -EPSILON)
        summaries[rule] = {
            "selected_cards": len(selected),
            "wins": wins,
            "losses": losses,
            "ties": len(selected) - wins - losses,
            "win_rate": wins / len(selected) if selected else None,
            "mean_actual_margin_return": sum(item[1] for item in selected) / len(selected) if selected else None,
            "aggregate_actual_margin_return": (
                sum(item[0] for item in selected)
                / sum(abs(item[0] / item[1]) for item in selected if abs(item[1]) > EPSILON)
                if selected and all(abs(item[1]) > EPSILON for item in selected)
                else None
            ),
        }
    return rows, summaries


def run(workspace: Path, output: Path, required_days: int = DEFAULT_REQUIRED_DAYS) -> dict[str, object]:
    second_root = workspace / ".artifacts" / "astra-second-study-20260913"
    light_root = workspace / ".artifacts" / "astra-light-study-20260913"
    control_root = workspace / ".artifacts" / "astra-control-study-20260913"
    protocol_path = workspace / ".artifacts" / "astra-edge-research-20260914" / "protocol.json"

    current = load_current_candidates(
        second_root / "report" / "ordinary_joined.csv",
        light_root / "standard_signal_samples.jsonl",
    )
    history = load_clock_history(control_root / "results" / "control_main.csv")

    predictions: list[dict[str, object]] = []
    path_rows: list[dict[str, object]] = []
    for trade in current:
        prediction, paths = forecast_one(trade, history, required_days=required_days)
        predictions.append(prediction)
        path_rows.extend(paths)

    choices = build_side_choices(predictions)
    output.mkdir(parents=True, exist_ok=True)
    predictions_path = output / "prior_predictions.csv"
    paths_path = output / "prior_projection_paths.csv"
    choices_path = output / "prior_side_choices.csv"
    write_csv(predictions_path, predictions)
    write_csv(paths_path, path_rows)
    write_csv(choices_path, choices)

    seal = {
        "schema": "astra_edge_prior_forecast_seal@1.0.0",
        "family": "F6",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_path": str(protocol_path),
        "protocol_sha256": sha256_file(protocol_path) if protocol_path.exists() else None,
        "required_training_days": required_days,
        "current_projection": "114 ordinary candidates, W2000 q10, two sides; no current settlement or pnl in forecast inputs",
        "history_source": "control_main.csv offset0, completed delivery dates only",
        "history_rule": "latest completed delivery dates available at current asof; nearest DTE per day, tie earlier entry",
        "prediction_rows": len(predictions),
        "projection_path_rows": len(path_rows),
        "available_prediction_rows": sum(1 for row in predictions if row.get("forecast_status") == "available"),
        "card_count": len({row["card_id"] for row in predictions}),
        "source_hashes": {
            "prior_predictions.csv": sha256_file(predictions_path),
            "prior_projection_paths.csv": sha256_file(paths_path),
            "prior_side_choices.csv": sha256_file(choices_path),
        },
    }
    seal_path = output / "forecast_seal.json"
    save_json(seal_path, seal)

    actual = load_actual_outcomes(second_root / "report" / "ordinary_joined.csv")
    evaluated_choices, evaluation_summary = attach_actual_results(choices, actual)
    evaluation_path = output / "prior_choice_evaluation.csv"
    write_csv(evaluation_path, evaluated_choices)
    evaluation = {
        "schema": "astra_edge_prior_evaluation@1.0.0",
        "forecast_seal_sha256": sha256_file(seal_path),
        "evaluation_rows": len(evaluated_choices),
        "evaluation_note": "Actual outcomes are attached only after forecast files and seal are written.",
        "rules": evaluation_summary,
        "source_hashes": {
            "prior_choice_evaluation.csv": sha256_file(evaluation_path),
        },
    }
    save_json(output / "evaluation_summary.json", evaluation)

    acceptance = {
        "schema": "astra_edge_prior_acceptance@1.0.0",
        "current_side_rows": len(predictions),
        "current_cards": len({row["card_id"] for row in predictions}),
        "required_training_days": required_days,
        "available_side_predictions": seal["available_prediction_rows"],
        "insufficient_side_predictions": sum(1 for row in predictions if row.get("forecast_status") != "available"),
        "projection_paths": len(path_rows),
        "forecast_sealed_before_actual_join": True,
        "choice_rules": ["higher_empirical_net_profit_probability", "higher_empirical_margin_return"],
        "tie_policy": "no_preference",
    }
    save_json(output / "acceptance.json", acceptance)
    return {**acceptance, "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-days", type=int, default=DEFAULT_REQUIRED_DAYS)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.output, args.required_days), ensure_ascii=False, sort_keys=True))
