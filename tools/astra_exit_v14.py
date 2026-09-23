"""Astra v1.4 direct two-leg exit research.

This module replays two frozen spot-trigger exit diagnostics on the saved v13
opportunity rows. It is a payoff/path study only. Historical executable option
quotes are unavailable, so actual EV, actual win rate and real delta remain
unknown even when a reference intrinsic scenario looks favorable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

SCHEMA = "astra_exit_v14@1.0.0"
LEDGER_SCHEMA = "astra_exit_v14_ledger@1.0.0"

RULES = ("SHORT_TOUCH_FIRST", "MID_WIDTH_INTRUSION_FIRST")
EXTRA_DEBIT_FRACTIONS = (0.0, 0.01, 0.025, 0.05)
MONITOR_MINUTES = 30
MONITOR_MS = MONITOR_MINUTES * 60 * 1000
EVALUATION_YEARS = (2022, 2023, 2024, 2025)

OPPORTUNITY_COLUMNS = [
    "row_id",
    "delivery_date",
    "year",
    "side",
    "actual_width",
    "row_weight",
    "actual_loss_normalized",
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
    "short_strike",
    "long_strike",
    "short_name",
    "long_name",
    "entry_price",
    "settlement_price",
    "payout_btc",
    "loss_normalized",
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
        return float(value) if math.isfinite(float(value)) else None
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


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def finite_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        out = int(float(value))
    except (TypeError, ValueError):
        return None
    return out


def ms_to_utc(ms: Any) -> str | None:
    value = finite_int(ms)
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def validate_spot_observation(obs: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Validate the archived spot decision observation for one 30m schedule point."""

    if obs is None:
        return False, "missing_observation"
    if int(obs.get("_duplicate_count", 1)) > 1:
        return False, "duplicate_as_of_observation"

    as_of_ms = finite_int(obs.get("as_of_ms"))
    price_observation_ms = finite_int(obs.get("price_observation_ms"))
    spot_open_time_ms = finite_int(obs.get("spot_open_time_ms"))
    last_closed_time_ms = finite_int(obs.get("last_closed_time_ms"))
    last_closed_price = finite_float(obs.get("last_closed_price"))

    if as_of_ms is None:
        return False, "missing_as_of_ms"
    if price_observation_ms is None:
        return False, "missing_price_observation_ms"
    if spot_open_time_ms is None:
        return False, "missing_spot_open_time_ms"
    if last_closed_price is None or last_closed_price <= 0:
        return False, "invalid_last_closed_price"
    if price_observation_ms >= as_of_ms:
        return False, "price_observation_not_before_as_of"
    if price_observation_ms != as_of_ms - 1:
        return False, "stale_closed_minute_not_latest"
    if spot_open_time_ms % 60000 != 0:
        return False, "spot_open_time_not_minute_aligned"
    if price_observation_ms - spot_open_time_ms != 59999:
        return False, "spot_minute_not_fully_closed"
    if last_closed_time_ms is not None and last_closed_time_ms != price_observation_ms:
        return False, "last_closed_time_mismatch"
    return True, None


def rule_threshold(row: Mapping[str, Any], rule: str) -> float:
    side = canonical_side(row["side"])
    short = float(row["short_strike"])
    width = float(row["actual_width"])
    if rule == "SHORT_TOUCH_FIRST":
        return short
    if rule == "MID_WIDTH_INTRUSION_FIRST":
        return short - 0.5 * width if side == "put_credit" else short + 0.5 * width
    raise ValueError(f"unknown exit rule: {rule}")


def spot_crosses_rule(side: str, spot: float, threshold: float) -> bool:
    canonical = canonical_side(side)
    if canonical == "put_credit":
        return spot <= threshold
    if canonical == "call_credit":
        return spot >= threshold
    raise AssertionError("unreachable")


def monitor_schedule(entry_ms: int, expiry_ms: int, monitor_ms: int = MONITOR_MS) -> Iterable[int]:
    scheduled = int(entry_ms) + int(monitor_ms)
    while scheduled < int(expiry_ms):
        yield scheduled
        scheduled += int(monitor_ms)


def evaluate_exit_path(
    row: Mapping[str, Any],
    observations_by_as_of: Mapping[int, Mapping[str, Any]],
    rule: str,
    monitor_ms: int = MONITOR_MS,
) -> dict[str, Any]:
    """Return the first frozen trigger or the first pre-trigger path gap.

    Terminal payout is deliberately not an input. This keeps trigger timing and
    mandatory scenario exit independent from hindsight outcome.
    """

    side = canonical_side(row["side"])
    entry_ms = int(row["entry_ms"])
    expiry_ms = int(row["expiry_ms"])
    threshold = rule_threshold(row, rule)

    for scheduled_as_of_ms in monitor_schedule(entry_ms, expiry_ms, monitor_ms):
        obs = observations_by_as_of.get(scheduled_as_of_ms)
        valid, reason = validate_spot_observation(obs)
        if not valid:
            return {
                "path_status": "path_unknown",
                "rule": rule,
                "unknown_as_of_ms": scheduled_as_of_ms,
                "unknown_as_of_utc": ms_to_utc(scheduled_as_of_ms),
                "path_unknown_reason": reason,
                "threshold": threshold,
            }
        spot = float(obs["last_closed_price"])
        if spot_crosses_rule(side, spot, threshold):
            price_observation_ms = int(obs["price_observation_ms"])
            return {
                "path_status": "triggered",
                "rule": rule,
                "trigger_as_of_ms": scheduled_as_of_ms,
                "trigger_as_of_utc": ms_to_utc(scheduled_as_of_ms),
                "trigger_price_observation_ms": price_observation_ms,
                "trigger_price_observation_utc": ms_to_utc(price_observation_ms),
                "trigger_spot": spot,
                "threshold": threshold,
                "observation_id": obs.get("observation_id"),
            }

    return {
        "path_status": "not_triggered",
        "rule": rule,
        "threshold": threshold,
    }


def intrinsic_spread_btc(side: str, spot: float, short_strike: float, long_strike: float) -> float:
    """Intrinsic BTC value of closing the original vertical spread at trigger spot."""

    if spot <= 0:
        raise ValueError("spot must be positive")
    canonical = canonical_side(side)
    if canonical == "put_credit":
        short_intrinsic = max(short_strike - spot, 0.0)
        long_intrinsic = max(long_strike - spot, 0.0)
    else:
        short_intrinsic = max(spot - short_strike, 0.0)
        long_intrinsic = max(spot - long_strike, 0.0)
    return max(short_intrinsic - long_intrinsic, 0.0) / spot


def original_width_btc(row: Mapping[str, Any]) -> float:
    width = float(row["actual_width"])
    entry_price = float(row["entry_price"])
    if width <= 0 or entry_price <= 0:
        raise ValueError("actual_width and entry_price must be positive")
    return width / entry_price


def reference_exit_cost_btc(row: Mapping[str, Any], trigger_spot: float, extra_fraction: float) -> float:
    intrinsic = intrinsic_spread_btc(
        row["side"],
        float(trigger_spot),
        float(row["short_strike"]),
        float(row["long_strike"]),
    )
    return intrinsic + float(extra_fraction) * original_width_btc(row)


def terminal_outcome_class(row: Mapping[str, Any]) -> str:
    loss = finite_float(row.get("loss_normalized"))
    protection = bool_or_none(row.get("protection_leg_breached"))
    if loss is None or loss <= 0:
        return "terminal_zero_payout"
    if protection is True or settlement_crosses_protection(row):
        return "terminal_full_payout"
    return "terminal_partial_payout"


def settlement_crosses_protection(row: Mapping[str, Any]) -> bool:
    settlement = finite_float(row.get("settlement_price"))
    long_strike = finite_float(row.get("long_strike"))
    if settlement is None or long_strike is None:
        return False
    side = canonical_side(row["side"])
    if side == "put_credit":
        return settlement <= long_strike
    return settlement >= long_strike


def build_exit_ledger_row(
    row: Mapping[str, Any],
    observations_by_as_of: Mapping[int, Mapping[str, Any]],
    rule: str,
    extra_fractions: Sequence[float] = EXTRA_DEBIT_FRACTIONS,
) -> dict[str, Any]:
    path = evaluate_exit_path(row, observations_by_as_of, rule)
    width_btc = original_width_btc(row)
    terminal_payout_btc = float(row["payout_btc"])
    terminal_loss_normalized = float(row["loss_normalized"])
    base: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "row_id": row["row_id"],
        "rule": rule,
        "delivery_date": row["delivery_date"],
        "year": int(row["year"]),
        "side": canonical_side(row["side"]),
        "entry_ms": int(row["entry_ms"]),
        "expiry_ms": int(row["expiry_ms"]),
        "short_name": row.get("short_name"),
        "long_name": row.get("long_name"),
        "short_strike": float(row["short_strike"]),
        "long_strike": float(row["long_strike"]),
        "actual_width": float(row["actual_width"]),
        "entry_price": float(row["entry_price"]),
        "row_weight": float(row["row_weight"]),
        "path_status": path["path_status"],
        "threshold": path.get("threshold"),
        "terminal_payout_btc": terminal_payout_btc,
        "terminal_loss_normalized": terminal_loss_normalized,
        "terminal_outcome_class": terminal_outcome_class(row),
        "affordable_all_in_exit_debit_btc": terminal_payout_btc,
        "affordable_all_in_exit_debit_normalized": terminal_payout_btc / width_btc,
        "actual_delta_btc": None,
        "actual_EV": None,
        "true_win_rate": None,
        "qualification": "insufficient_executable_exit_quotes",
    }
    base.update({key: path.get(key) for key in path if key not in {"rule", "path_status", "threshold"}})

    if path["path_status"] == "triggered":
        trigger_spot = float(path["trigger_spot"])
        intrinsic = intrinsic_spread_btc(
            row["side"],
            trigger_spot,
            float(row["short_strike"]),
            float(row["long_strike"]),
        )
        base["quote_status"] = "exit_price_unavailable"
        base["triggered_quote_unavailable"] = True
        base["immediate_intrinsic_btc"] = intrinsic
        base["immediate_intrinsic_normalized"] = intrinsic / width_btc
        for extra in extra_fractions:
            suffix = extra_suffix(extra)
            cost = reference_exit_cost_btc(row, trigger_spot, extra)
            base[f"reference_cost_btc_{suffix}"] = cost
            base[f"reference_cost_normalized_{suffix}"] = cost / width_btc
            base[f"reference_gain_exit_minus_hold_btc_{suffix}"] = terminal_payout_btc - cost
            base[f"reference_gain_exit_minus_hold_normalized_{suffix}"] = (
                terminal_payout_btc - cost
            ) / width_btc
    elif path["path_status"] == "not_triggered":
        base["quote_status"] = "not_applicable_no_trigger"
        base["triggered_quote_unavailable"] = False
        base["immediate_intrinsic_btc"] = None
        base["immediate_intrinsic_normalized"] = None
        for extra in extra_fractions:
            suffix = extra_suffix(extra)
            base[f"reference_cost_btc_{suffix}"] = terminal_payout_btc
            base[f"reference_cost_normalized_{suffix}"] = terminal_payout_btc / width_btc
            base[f"reference_gain_exit_minus_hold_btc_{suffix}"] = 0.0
            base[f"reference_gain_exit_minus_hold_normalized_{suffix}"] = 0.0
    else:
        base["quote_status"] = "path_unknown"
        base["triggered_quote_unavailable"] = False
        base["immediate_intrinsic_btc"] = None
        base["immediate_intrinsic_normalized"] = None
        for extra in extra_fractions:
            suffix = extra_suffix(extra)
            base[f"reference_cost_btc_{suffix}"] = None
            base[f"reference_cost_normalized_{suffix}"] = None
            base[f"reference_gain_exit_minus_hold_btc_{suffix}"] = None
            base[f"reference_gain_exit_minus_hold_normalized_{suffix}"] = None

    return base


def extra_suffix(extra_fraction: float) -> str:
    text = f"{float(extra_fraction):.3f}".rstrip("0").rstrip(".")
    return "k" + text.replace(".", "p")


def weighted_mean(values: Sequence[Any], weights: Sequence[Any]) -> float | None:
    total = 0.0
    acc = 0.0
    for value, weight in zip(values, weights):
        v = finite_float(value)
        w = finite_float(weight)
        if v is not None and w is not None and w > 0:
            acc += v * w
            total += w
    return acc / total if total > 0 else None


def weighted_es95(values: Sequence[Any], weights: Sequence[Any]) -> float | None:
    pairs = []
    for value, weight in zip(values, weights):
        v = finite_float(value)
        w = finite_float(weight)
        if v is not None and w is not None and w > 0:
            pairs.append((v, w))
    if not pairs:
        return None
    pairs.sort(key=lambda pair: pair[0], reverse=True)
    total_weight = sum(weight for _, weight in pairs)
    target = total_weight * 0.05
    if target <= 0:
        return None
    remaining = target
    acc = 0.0
    used = 0.0
    for value, weight in pairs:
        take = min(weight, remaining)
        acc += value * take
        used += take
        remaining -= take
        if remaining <= 1e-15:
            break
    return acc / used if used > 0 else None


def summarize_exit_ledger(
    ledger: pd.DataFrame,
    extra_fractions: Sequence[float] = EXTRA_DEBIT_FRACTIONS,
) -> dict[str, Any]:
    yearly_groups = []
    pooled_groups = []

    for keys, group in ledger.groupby(["rule", "year", "side"], dropna=False, sort=True):
        rule, year, side = keys
        yearly_groups.append(
            summarize_exit_group(
                group,
                rule=str(rule),
                side=str(side),
                year=int(year),
                extra_fractions=extra_fractions,
            )
        )

    for keys, group in ledger.groupby(["rule", "side"], dropna=False, sort=True):
        rule, side = keys
        pooled_groups.append(
            summarize_exit_group(
                group,
                rule=str(rule),
                side=str(side),
                year="pooled",
                extra_fractions=extra_fractions,
            )
        )

    return {
        "schema": SCHEMA,
        "qualification": "insufficient_executable_exit_quotes",
        "actual_effect_support": False,
        "actual_delta_btc": None,
        "actual_EV": None,
        "true_win_rate": None,
        "candidate_rules": list(RULES),
        "extra_debit_fractions": [float(x) for x in extra_fractions],
        "actual_effect_gates": noquote_actual_effect_gates(),
        "reference_gain_estimate_scope": (
            "known_path_contribution_only; path_unknown is reported separately "
            "and is not treated as zero-gain or a full-strategy estimate"
        ),
        "groups": yearly_groups,
        "pooled_rule_side_groups": pooled_groups,
    }


def summarize_exit_group(
    group: pd.DataFrame,
    rule: str,
    side: str,
    year: int | str,
    extra_fractions: Sequence[float],
) -> dict[str, Any]:
        total_weight = float(group["row_weight"].sum())
        triggered = group[group["path_status"] == "triggered"]
        not_triggered = group[group["path_status"] == "not_triggered"]
        unknown = group[group["path_status"] == "path_unknown"]
        known = group[group["path_status"].isin(["triggered", "not_triggered"])]
        item: dict[str, Any] = {
            "rule": rule,
            "year": year,
            "side": side,
            "original_weight": total_weight,
            "row_count": int(len(group)),
            "triggered_weight": float(triggered["row_weight"].sum()),
            "not_triggered_weight": float(not_triggered["row_weight"].sum()),
            "path_unknown_weight": float(unknown["row_weight"].sum()),
            "known_path_weight": float(known["row_weight"].sum()),
            "trigger_coverage_original": safe_div(float(triggered["row_weight"].sum()), total_weight),
            "known_path_coverage_original": safe_div(float(known["row_weight"].sum()), total_weight),
            "quote_unavailable_weight": float(triggered["row_weight"].sum()),
            "actual_effect_support": False,
            "qualification": "insufficient_executable_exit_quotes",
            "actual_delta_btc": None,
            "actual_EV": None,
            "true_win_rate": None,
            "actual_effect_gates": noquote_actual_effect_gates(),
            "mean_affordable_all_in_exit_debit_normalized": weighted_mean(
                group["affordable_all_in_exit_debit_normalized"].tolist(),
                group["row_weight"].tolist(),
            ),
            "affordable_all_in_exit_debit_es95_normalized": weighted_es95(
                group["affordable_all_in_exit_debit_normalized"].tolist(),
                group["row_weight"].tolist(),
            ),
            "hold_es95_normalized": weighted_es95(
                known["terminal_loss_normalized"].tolist(),
                known["row_weight"].tolist(),
            ),
            "triggered_later_zero_weight": float(
                triggered.loc[
                    triggered["terminal_outcome_class"] == "terminal_zero_payout",
                    "row_weight",
                ].sum()
            ),
            "triggered_later_partial_weight": float(
                triggered.loc[
                    triggered["terminal_outcome_class"] == "terminal_partial_payout",
                    "row_weight",
                ].sum()
            ),
            "triggered_later_full_weight": float(
                triggered.loc[
                    triggered["terminal_outcome_class"] == "terminal_full_payout",
                    "row_weight",
                ].sum()
            ),
        }
        for extra in extra_fractions:
            suffix = extra_suffix(extra)
            gain_col = f"reference_gain_exit_minus_hold_normalized_{suffix}"
            cost_col = f"reference_cost_normalized_{suffix}"
            triggered_gain_sum = float(
                (
                    triggered[gain_col].astype(float)
                    * triggered["row_weight"].astype(float)
                ).sum()
            ) if len(triggered) else 0.0
            known_gain_sum = float(
                (
                    known[gain_col].astype(float)
                    * known["row_weight"].astype(float)
                ).sum()
            ) if len(known) else 0.0
            item[f"conditional_triggered_mean_reference_gain_{suffix}"] = safe_div(
                triggered_gain_sum,
                float(triggered["row_weight"].sum()),
            )
            item[f"known_path_mean_reference_gain_{suffix}"] = safe_div(
                known_gain_sum,
                float(known["row_weight"].sum()),
            )
            item[f"known_path_per_original_reference_gain_contribution_{suffix}"] = safe_div(
                triggered_gain_sum,
                total_weight,
            )
            item[f"reference_cost_es95_normalized_{suffix}"] = weighted_es95(
                known[cost_col].tolist(),
                known["row_weight"].tolist(),
            )
        return item


def noquote_actual_effect_gates() -> dict[str, Any]:
    return {
        "status": "unassessable_noquotes",
        "quoted_trigger_dates": 0,
        "trigger_quote_coverage": 0.0,
        "actual_gain_ci": None,
        "mean_actual_gain": None,
        "annual_nonworse": None,
        "actual_policy_es95": None,
        "without_ten_best_gain_dates": None,
        "support": False,
    }


def safe_div(numerator: float, denominator: float) -> float | None:
    if denominator and math.isfinite(denominator):
        return numerator / denominator
    return None


def validate_executable_exit_quote(row: Mapping[str, Any], quote: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a same-instrument quote fixture and compute the real close debit.

    This helper is intentionally not used to fill history unless valid archived
    two-leg quotes are supplied. It encodes the executable gate from protocol v1.4.
    """

    reasons = []
    expected_short = str(row.get("short_name") or "")
    expected_long = str(row.get("long_name") or "")
    if str(quote.get("short_name") or "") != expected_short:
        reasons.append("short_instrument_mismatch")
    if str(quote.get("long_name") or "") != expected_long:
        reasons.append("long_instrument_mismatch")

    size = finite_float(quote.get("size"))
    short_ask_depth = finite_float(quote.get("short_ask_depth"))
    long_bid_depth = finite_float(quote.get("long_bid_depth"))
    short_ask = finite_float(quote.get("short_ask_btc"))
    long_bid = finite_float(quote.get("long_bid_btc"))
    short_quote_ms = finite_int(quote.get("short_quote_ms"))
    long_quote_ms = finite_int(quote.get("long_quote_ms"))
    received_ms = finite_int(quote.get("received_ms"))
    trigger_ms = finite_int(row.get("trigger_as_of_ms"))
    expiry_ms = finite_int(row.get("expiry_ms"))
    exit_fees_btc = finite_float(quote.get("exit_fees_btc"))
    hold_delivery_fees_btc = finite_float(quote.get("hold_delivery_fees_btc"))

    if size is None or size < 1:
        reasons.append("size_lt_one")
    if short_ask_depth is None or short_ask_depth < 1:
        reasons.append("short_ask_depth_lt_one")
    if long_bid_depth is None or long_bid_depth < 1:
        reasons.append("long_bid_depth_lt_one")
    if short_ask is None or short_ask <= 0:
        reasons.append("invalid_short_ask")
    if long_bid is None or long_bid <= 0:
        reasons.append("invalid_long_bid")
    if exit_fees_btc is None or exit_fees_btc < 0:
        reasons.append("invalid_exit_fees_btc")
    if hold_delivery_fees_btc is None or hold_delivery_fees_btc < 0:
        reasons.append("invalid_hold_delivery_fees_btc")
    if None in {short_quote_ms, long_quote_ms, received_ms, trigger_ms, expiry_ms}:
        reasons.append("missing_quote_timestamps")
    else:
        assert short_quote_ms is not None
        assert long_quote_ms is not None
        assert received_ms is not None
        assert trigger_ms is not None
        assert expiry_ms is not None
        if short_quote_ms < trigger_ms or long_quote_ms < trigger_ms:
            reasons.append("quote_before_trigger")
        if received_ms < short_quote_ms or received_ms < long_quote_ms:
            reasons.append("received_before_quote")
        if received_ms - trigger_ms > 60000:
            reasons.append("receipt_delay_gt_60s")
        if abs(short_quote_ms - long_quote_ms) > 2000:
            reasons.append("leg_quote_skew_gt_2s")
        if (
            trigger_ms >= expiry_ms
            or short_quote_ms >= expiry_ms
            or long_quote_ms >= expiry_ms
            or received_ms >= expiry_ms
        ):
            reasons.append("expiry_passed")

    if reasons:
        return {
            "valid": False,
            "reasons": reasons,
            "actual_delta_btc": None,
        }

    exit_debit_btc = float(short_ask) - float(long_bid) + exit_fees_btc - hold_delivery_fees_btc
    terminal_payout_btc = finite_float(row.get("payout_btc"))
    actual_delta_btc = None if terminal_payout_btc is None else terminal_payout_btc - exit_debit_btc
    return {
        "valid": True,
        "reasons": [],
        "exit_debit_btc": exit_debit_btc,
        "actual_delta_btc": actual_delta_btc,
        "actual_EV": None,
        "true_win_rate": None,
    }


def verify_protocol_sources(protocol_path: Path) -> dict[str, Any]:
    protocol_path = Path(protocol_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol_sha256 = digest_file(protocol_path)

    seal_path = protocol_path.with_name("protocol_seal.json")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if seal.get("protocol_sha256") != protocol_sha256:
        raise ValueError("protocol_seal_hash_mismatch")

    manifest_path = protocol_path.with_name("source_manifest.json")
    source_manifest_sha256 = digest_file(manifest_path)
    expected_manifest_sha = protocol.get("source_manifest_sha256")
    if expected_manifest_sha != source_manifest_sha256:
        raise ValueError("source_manifest_hash_mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    checked_files = []
    for file_name, meta in manifest.get("files", {}).items():
        path = Path(file_name)
        if not path.exists():
            raise FileNotFoundError(file_name)
        actual_size = path.stat().st_size
        expected_size = int(meta["bytes"])
        if actual_size != expected_size:
            raise ValueError(f"source_size_mismatch: {file_name}")
        actual_hash = digest_file(path)
        if actual_hash != meta["sha256"]:
            raise ValueError(f"source_hash_mismatch: {file_name}")
        checked_files.append({"path": file_name, "sha256": actual_hash, "bytes": actual_size})

    amendment_path = protocol_path.with_name("preresult_amendment_01.json")
    amendment_sha256 = None
    amendment = None
    if amendment_path.exists():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        amendment_sha256 = digest_file(amendment_path)
        if amendment.get("original_protocol_sha256") != protocol_sha256:
            raise ValueError("preresult_amendment_protocol_hash_mismatch")

    return {
        "protocol_sha256": protocol_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "protocol_seal_sha256": digest_file(seal_path),
        "preresult_amendment_01_sha256": amendment_sha256,
        "checked_file_count": len(checked_files),
        "checked_files": checked_files,
        "protocol": protocol,
        "source_manifest": manifest,
        "preresult_amendment_01": amendment,
    }


def require_manifest_coverage(verification: Mapping[str, Any], paths: Sequence[Path]) -> dict[str, Any]:
    manifest_files = verification["source_manifest"].get("files", {})
    by_resolved: dict[str, dict[str, Any]] = {}
    for file_name, meta in manifest_files.items():
        manifest_path = Path(file_name)
        resolved = str(manifest_path.resolve()).casefold()
        by_resolved[resolved] = {
            "path": str(manifest_path),
            "sha256": meta["sha256"],
            "bytes": int(meta["bytes"]),
        }

    identities: dict[str, Any] = {}
    missing: list[str] = []
    for path in paths:
        resolved = str(Path(path).resolve()).casefold()
        identity = by_resolved.get(resolved)
        if identity is None:
            missing.append(str(path))
        else:
            identities[str(path)] = identity
    if missing:
        raise ValueError(f"input_not_in_source_manifest: {missing}")
    return identities


def find_opportunity_rows_path(research_v13: Path) -> Path:
    candidates = [
        research_v13 / "row_scores_main_membership.csv",
        research_v13 / "opportunity_02" / "row_scores_main_membership.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("row_scores_main_membership.csv not found under research-v13")


def load_opportunity_rows(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=lambda col: col in OPPORTUNITY_COLUMNS)
    missing = set(OPPORTUNITY_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"opportunity_missing_columns: {sorted(missing)}")
    frame = frame[frame["year"].isin(EVALUATION_YEARS)].copy()
    frame["row_id"] = frame["row_id"].astype(str)
    frame["side"] = frame["side"].map(canonical_side)
    if frame["row_id"].duplicated().any():
        duplicates = frame.loc[frame["row_id"].duplicated(), "row_id"].head(5).tolist()
        raise ValueError(f"duplicate_opportunity_row_id: {duplicates}")
    return frame


def load_model_rows(model_input_dir: Path, row_ids: set[str]) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    for year in EVALUATION_YEARS:
        path = model_input_dir / f"model_rows-{year}.csv"
        for chunk in pd.read_csv(
            path,
            usecols=lambda col: col in MODEL_INPUT_COLUMNS,
            dtype={"row_id": "string"},
            chunksize=200_000,
        ):
            selected = chunk[chunk["row_id"].astype(str).isin(row_ids)]
            if not selected.empty:
                chunks.append(selected.copy())
    if not chunks:
        raise ValueError("no_model_rows_loaded")
    frame = pd.concat(chunks, ignore_index=True)
    frame["row_id"] = frame["row_id"].astype(str)
    missing = set(MODEL_INPUT_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"model_input_missing_columns: {sorted(missing)}")
    if frame["row_id"].duplicated().any():
        duplicates = frame.loc[frame["row_id"].duplicated(), "row_id"].head(5).tolist()
        raise ValueError(f"duplicate_model_row_id: {duplicates}")
    return frame


def join_opportunity_model_rows(opportunity: pd.DataFrame, model_rows: pd.DataFrame) -> pd.DataFrame:
    merged = opportunity.merge(model_rows, on="row_id", how="left", suffixes=("_opp", ""))
    if merged["entry_ms"].isna().any():
        missing = merged.loc[merged["entry_ms"].isna(), "row_id"].head(5).tolist()
        raise ValueError(f"model_row_missing_for_opportunity: {missing}")
    if len(merged) != len(opportunity):
        raise ValueError("opportunity_model_join_not_1_to_1")

    merged["side_model"] = merged["side"].map(canonical_side)
    side_mismatch = merged["side_opp"].map(canonical_side) != merged["side_model"]
    if side_mismatch.any():
        rows = merged.loc[side_mismatch, "row_id"].head(5).tolist()
        raise ValueError(f"side_mismatch: {rows}")
    width_diff = (merged["actual_width_opp"].astype(float) - merged["actual_width"].astype(float)).abs()
    if (width_diff > 1e-9).any():
        rows = merged.loc[width_diff > 1e-9, "row_id"].head(5).tolist()
        raise ValueError(f"actual_width_mismatch: {rows}")
    loss_diff = (
        merged["actual_loss_normalized"].astype(float)
        - merged["loss_normalized"].astype(float)
    ).abs()
    if (loss_diff > 1e-9).any():
        rows = merged.loc[loss_diff > 1e-9, "row_id"].head(5).tolist()
        raise ValueError(f"actual_loss_mismatch: {rows}")

    merged["side"] = merged["side_model"]
    merged["actual_width"] = merged["actual_width"].astype(float)
    merged["year"] = merged["year"].astype(int)
    merged["row_weight"] = merged["row_weight"].astype(float)
    return merged


def build_needed_monitor_times(rows: pd.DataFrame) -> set[int]:
    times: set[int] = set()
    for entry_ms, expiry_ms in zip(rows["entry_ms"], rows["expiry_ms"]):
        times.update(monitor_schedule(int(entry_ms), int(expiry_ms)))
    return times


def load_market_observations(path: Path, needed_times: set[int]) -> dict[int, dict[str, Any]]:
    observations: dict[int, dict[str, Any]] = {}
    if not needed_times:
        return observations
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obs = json.loads(line)
            as_of_ms = finite_int(obs.get("as_of_ms"))
            if as_of_ms in needed_times:
                if as_of_ms in observations:
                    observations[as_of_ms]["_duplicate_count"] = int(
                        observations[as_of_ms].get("_duplicate_count", 1)
                    ) + 1
                else:
                    observations[as_of_ms] = obs
                    observations[as_of_ms]["_duplicate_count"] = 1
    return observations


def run_exit_research(
    research_v11: Path,
    research_v13: Path,
    protocol_path: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)

    verification = verify_protocol_sources(protocol_path)
    opportunity_path = find_opportunity_rows_path(research_v13)
    model_input_dir = research_v11 / "step30" / "model_input"
    model_paths = [model_input_dir / f"model_rows-{year}.csv" for year in EVALUATION_YEARS]
    observations_path = research_v11 / "step30" / "decisions" / "market_observations.jsonl"
    input_hashes = require_manifest_coverage(
        verification,
        [opportunity_path, *model_paths, observations_path],
    )

    opportunity = load_opportunity_rows(opportunity_path)
    model_rows = load_model_rows(model_input_dir, set(opportunity["row_id"]))
    rows = join_opportunity_model_rows(opportunity, model_rows)
    needed_times = build_needed_monitor_times(rows)
    observations = load_market_observations(observations_path, needed_times)

    ledger_rows: list[dict[str, Any]] = []
    for _, record in rows.iterrows():
        record_dict = record.to_dict()
        for rule in RULES:
            ledger_rows.append(build_exit_ledger_row(record_dict, observations, rule))

    ledger = pd.DataFrame(ledger_rows)
    ledger_path = output / "exit_first_trigger_ledger.csv"
    ledger.to_csv(ledger_path, index=False, encoding="utf-8")

    summary = summarize_exit_ledger(ledger)
    summary.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_verification": {
                key: verification[key]
                for key in [
                    "protocol_sha256",
                    "source_manifest_sha256",
                    "protocol_seal_sha256",
                    "preresult_amendment_01_sha256",
                    "checked_file_count",
                ]
            },
            "input_hashes": input_hashes,
            "row_count": int(len(rows)),
            "ledger_row_count": int(len(ledger)),
            "needed_monitor_time_count": int(len(needed_times)),
            "loaded_monitor_observation_count": int(len(observations)),
            "output_files": {
                "ledger": str(ledger_path),
            },
            "outcome_blindness": {
                "trigger_uses_terminal_payout": False,
                "reference_exit_cost_uses_terminal_payout": False,
                "terminal_payout_use": "retrospective hold comparison and affordable all-in exit boundary only",
            },
        }
    )

    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_summary_md(output / "summary.md", summary)
    return summary


def write_summary_md(path: Path, summary: Mapping[str, Any]) -> None:
    lines = [
        "# Astra v1.4 direct exit research",
        "",
        f"- schema: `{summary.get('schema')}`",
        f"- qualification: `{summary.get('qualification')}`",
        f"- actual effect support: `{summary.get('actual_effect_support')}`",
        f"- amendment hash: `{summary.get('source_verification', {}).get('preresult_amendment_01_sha256')}`",
        "- actual_delta_btc / actual_EV / true_win_rate remain null because executable historical two-leg quotes are unavailable.",
        "- All real economic effect gates are `unassessable_noquotes`; quote dates and executable quote coverage are zero.",
        "- Reference gain fields are known-path contributions only; path_unknown is not treated as zero-gain strategy performance.",
        "- Trigger time and reference scenario exit cost are outcome-blind; terminal payout is used only after the frozen trigger for hold comparison and cost boundary.",
        "",
        "## Group rows",
        "",
        "| rule | year | side | triggered wt | unknown wt | k0 mean gain triggered |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for item in summary.get("groups", []):
        lines.append(
            "| {rule} | {year} | {side} | {trig} | {unk} | {gain} |".format(
                rule=item.get("rule"),
                year=item.get("year"),
                side=item.get("side"),
                trig=format_optional(item.get("triggered_weight")),
                unk=format_optional(item.get("path_unknown_weight")),
                gain=format_optional(item.get("conditional_triggered_mean_reference_gain_k0")),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_optional(value: Any) -> str:
    v = finite_float(value)
    return "" if v is None else f"{v:.10g}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-v11", required=True, type=Path)
    parser.add_argument("--research-v13", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_preexisted = args.output.exists()
    try:
        run_exit_research(args.research_v11, args.research_v13, args.protocol, args.output)
    except Exception as exc:  # pragma: no cover - exercised manually in full runs.
        failure = {
            "schema": "astra_exit_v14_failure@1.0.0",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        if output_preexisted:
            raise
        if not args.output.exists():
            args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "failure.json").write_text(
            json.dumps(json_safe(failure), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
