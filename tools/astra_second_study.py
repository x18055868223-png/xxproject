"""Second-stage Astra light study.

This module is research-only.  It reads the frozen first-stage sample set and
market inputs, then writes ordinary-session and weekend scenario outputs into a
separate artifact directory.  It does not call network services, LLMs, FMZ, or
production materializers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import astra_light_study_analysis as base


BJT = timezone(timedelta(hours=8))
try:
    NY = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    # The frozen study window is June-September 2026, which is entirely EDT.
    NY = timezone(timedelta(hours=-4), name="America/New_York_EDT")
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
MAIN_WIDTH = 2000.0
MAIN_CREDIT = 0.10
SIDE_PUT = base.SIDE_PUT
SIDE_CALL = base.SIDE_CALL


def run_second_study(first_study_dir: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = _read_json(config_path)
    _verify_inputs(config, first_study_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = _read_jsonl(first_study_dir / "standard_signal_samples.jsonl")
    first_rows = [_typed_spread_row(row) for row in _read_csv(first_study_dir / "light_study_results.csv")]
    klines = _read_csv(first_study_dir / "market" / "klines.csv")
    kline_by_open = base._index_klines(klines)
    instruments = _load_study_instruments(first_study_dir)
    instruments_by_expiry = base._index_instruments(instruments)
    deliveries = _read_json(first_study_dir / "market" / "delivery_prices.json")
    delivery_by_date = base._index_deliveries(deliveries)

    ordinary = build_ordinary_outputs(samples, first_rows, config)
    ordinary_ids = {row["card_id"] for row in ordinary["card_index"]}
    weekend = build_weekend_outputs(samples, kline_by_open, instruments_by_expiry, delivery_by_date, config, ordinary_ids)

    _write_csv(output_dir / "ordinary_results.csv", ordinary["trades"])
    _write_csv(output_dir / "ordinary_excluded_cards.csv", ordinary["excluded_cards"])
    _write_csv(output_dir / "ordinary_card_index.csv", ordinary["card_index"])
    _write_csv(output_dir / "ordinary_group_summary.csv", ordinary["group_summary"])
    _write_csv(output_dir / "ordinary_session_summary.csv", ordinary["session_summary"])
    _write_csv(output_dir / "ordinary_session_overview.csv", ordinary["session_overview"])
    _write_csv(output_dir / "ordinary_tenor_summary.csv", ordinary["tenor_summary"])
    _write_csv(output_dir / "ordinary_ny_calendar_summary.csv", ordinary["ny_calendar_summary"])

    _write_csv(output_dir / "weekend_observations.csv", weekend["observations"])
    _write_csv(output_dir / "weekend_results.csv", weekend["trades"])
    _write_csv(output_dir / "weekend_group_summary.csv", weekend["group_summary"])
    _write_csv(output_dir / "weekend_age_summary.csv", weekend["age_summary"])
    _write_csv(output_dir / "weekend_volatility_pairs.csv", weekend["volatility_pairs"])

    eligibility = {
        "schema": "astra_second_study_eligibility@1.0.0",
        "ordinary": ordinary["eligibility"],
        "weekend": weekend["eligibility"],
        "source_counts": {
            "events": len(samples),
            "first_stage_spread_rows": len(first_rows),
        },
        "boundaries": config.get("boundaries", []),
    }
    summary = {
        "schema": "astra_second_study_analysis@1.0.0",
        "config_schema": config.get("schema"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_study_dir": str(first_study_dir),
        "output_dir": str(output_dir),
        "ordinary": {
            "eligibility": ordinary["eligibility"],
            "main": ordinary["main_summary"],
            "session_counts": ordinary["session_counts"],
            "session_overview": ordinary["session_overview"],
            "direction_counts": ordinary["direction_counts"],
        },
        "weekend": {
            "eligibility": weekend["eligibility"],
            "main": weekend["main_summary"],
            "volatility_comparison": weekend["volatility_summary"],
        },
        "margin": {
            "lambda": config["margin"]["lambda"],
            "lambda_sensitivity_multipliers": config["margin"]["lambda_sensitivity_multipliers"],
            "basis": config["margin"]["name"],
        },
        "notes": [
            "Ordinary rows exclude DTE <= 8 hours and keep no return results for excluded cards.",
            "Weekend rows use Saturday 09:01 BJT entry and Monday 16:00 BJT expiry, with legs reselected at weekend entry.",
            "Intrusion means expiry net PnL < 0 after inverse two-leg payout and assumed net credit.",
            "APR is a single-case calibrated margin scenario, not historical actual account APR.",
        ],
    }
    _write_json(output_dir / "eligibility.json", eligibility)
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "analysis_manifest.json", _manifest(output_dir))
    _validate_outputs(config, ordinary, weekend)
    return summary


def build_ordinary_outputs(
    samples: list[dict[str, Any]],
    first_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    card_base_rows = {}
    for row in first_rows:
        if row["target_width"] == MAIN_WIDTH and row["credit_fraction"] == MAIN_CREDIT and row["side"] == SIDE_PUT:
            card_base_rows[row["card_id"]] = row

    ordinary_cards: dict[str, dict[str, Any]] = {}
    excluded_cards = []
    for sample in samples:
        card_id = sample["card_id"]
        base_row = card_base_rows.get(card_id)
        if base_row is None:
            excluded_cards.append(_card_exclusion(sample, "missing_first_stage_result", None))
            continue
        dte_hours = base_row["dte_hours"]
        if dte_hours <= config["ordinary_dte_hours"]["greater_than"]:
            excluded_cards.append(_card_exclusion(sample, "dte_at_most_8_hours", base_row))
            continue
        if dte_hours > config["ordinary_dte_hours"]["at_most"]:
            excluded_cards.append(_card_exclusion(sample, "dte_above_24_hours", base_row))
            continue
        ordinary_cards[card_id] = _ordinary_card_projection(sample, base_row, config)

    trades = []
    for row in first_rows:
        card = ordinary_cards.get(row["card_id"])
        if card is None:
            continue
        projected = _decorate_trade(row, config, cohort="ordinary")
        projected.update({
            "session_bjt": card["session_bjt"],
            "entry_time_bjt": card["entry_time_bjt"],
            "entry_date_bjt": card["entry_date_bjt"],
            "ny_date": card["ny_date"],
            "ny_is_trading_day": card["ny_is_trading_day"],
            "ny_market_closed_reason": card["ny_market_closed_reason"],
        })
        trades.append(projected)

    card_index = list(ordinary_cards.values())
    base_main = _main_rows(trades)
    session_counts = _with_expected_zeroes(
        Counter(card["session_bjt"] for card in card_index),
        [row["name"] for row in config["sessions_bjt"]],
    )
    output = {
        "trades": trades,
        "excluded_cards": excluded_cards,
        "card_index": card_index,
        "group_summary": _summary_rows(trades, ("direction", "relation", "side", "target_width", "credit_fraction"), config),
        "session_summary": _summary_rows(base_main, ("session_bjt", "direction", "relation", "side"), config),
        "session_overview": _session_overview(card_index, base_main, config),
        "tenor_summary": _summary_rows(base_main, ("dte_bucket", "direction", "relation", "side"), config),
        "ny_calendar_summary": _summary_rows(base_main, ("ny_is_trading_day", "direction", "relation", "side"), config),
        "session_counts": session_counts,
        "direction_counts": dict(Counter(card["direction"] for card in card_index)),
        "main_summary": {
            "directional_vs_opposite": _summary_rows(
                [row for row in base_main if row["relation"] in {"directional", "opposite"}],
                ("relation", "side"),
                config,
            ),
            "neutral_sides": _summary_rows(
                [row for row in base_main if row["relation"] in {"neutral_put", "neutral_call"}],
                ("relation", "side"),
                config,
            ),
            "sessions": _summary_rows(base_main, ("session_bjt", "relation", "side"), config),
            "ny_calendar": _summary_rows(base_main, ("ny_is_trading_day", "relation", "side"), config),
        },
        "eligibility": {
            "input_events": len(samples),
            "ordinary_cards": len(card_index),
            "excluded_cards": len(excluded_cards),
            "excluded_by_reason": dict(Counter(row["reason"] for row in excluded_cards)),
            "trade_rows": len(trades),
            "main_scenario_trade_rows": len(base_main),
            "expected_session_counts": {row["name"]: row["expected"] for row in config["sessions_bjt"]},
            "actual_session_counts": session_counts,
            "independent_delivery_dates": len({row["delivery_date"] for row in base_main}),
            "independent_entry_dates_bjt": len({row["entry_date_bjt"] for row in base_main}),
        },
    }
    return output


def build_weekend_outputs(
    samples: list[dict[str, Any]],
    kline_by_open: dict[int, dict[str, Any]],
    instruments_by_expiry: dict[int, list[dict[str, Any]]],
    delivery_by_date: dict[str, float],
    config: dict[str, Any],
    ordinary_ids: set[str] | None = None,
) -> dict[str, Any]:
    sorted_samples = sorted(samples, key=lambda row: (row["event_time_ms"], row["card_id"]))
    ordinary_ids = ordinary_ids or set()
    observations = []
    trades = []
    volatility_pairs = []
    for saturday in _study_saturdays(sorted_samples, config["original_cutoff_ms"]):
        observed_at = _bjt_ms(datetime.combine(saturday, time(9, 0), tzinfo=BJT))
        entry_ms = _bjt_ms(datetime.combine(saturday, time(9, 1), tzinfo=BJT))
        expiry_ms = _bjt_ms(datetime.combine(saturday + timedelta(days=2), time(16, 0), tzinfo=BJT))
        source = _latest_sample_before(sorted_samples, observed_at)
        row = _weekend_observation_base(saturday, observed_at, entry_ms, expiry_ms, source, config)
        if source is None:
            row["status"] = "no_prior_signal"
            observations.append(row)
            continue
        row["source_in_ordinary_114"] = source["card_id"] in ordinary_ids
        entry_kline = kline_by_open.get(entry_ms)
        if entry_kline is None or not _positive(entry_kline.get("open")):
            row["status"] = "missing_entry_kline"
            observations.append(row)
            continue
        entry_price = float(entry_kline["open"])
        row["entry_price"] = entry_price
        row["expiry_matured"] = expiry_ms <= config["original_cutoff_ms"] and _utc_date(expiry_ms) in delivery_by_date
        row["delivery_price"] = delivery_by_date.get(_utc_date(expiry_ms))
        row.update(_path_window_metrics(kline_by_open, entry_ms, expiry_ms, entry_price, "weekend"))
        control_start = _bjt_ms(datetime.combine(saturday + timedelta(days=3), time(9, 1), tzinfo=BJT))
        control_end = _bjt_ms(datetime.combine(saturday + timedelta(days=5), time(16, 0), tzinfo=BJT))
        row["control_start_ms"] = control_start
        row["control_end_ms"] = control_end
        row["control_start_bjt"] = _iso_bjt(control_start)
        row["control_end_bjt"] = _iso_bjt(control_end)
        control = _path_window_metrics(kline_by_open, control_start, control_end, _open_price(kline_by_open, control_start), "control")
        row.update(control)
        if row.get("weekend_path_complete") and row.get("control_path_complete"):
            volatility_pairs.append({
                "observation_date_bjt": row["observation_date_bjt"],
                "source_card_id": row["source_card_id"],
                "weekend_range_pct": row["weekend_range_pct"],
                "control_range_pct": row["control_range_pct"],
                "weekend_abs_return_pct": row["weekend_abs_return_pct"],
                "control_abs_return_pct": row["control_abs_return_pct"],
                "weekend_lower_range": row["weekend_range_pct"] < row["control_range_pct"],
                "range_ratio_weekend_to_control": _safe_div(row["weekend_range_pct"], row["control_range_pct"]),
            })
        delivery_price = row["delivery_price"] if row["expiry_matured"] else None
        scenario_rows, issues = _scenario_rows_for_entry(
            sample=source,
            entry_ms=entry_ms,
            entry_price=entry_price,
            expiry_ms=expiry_ms,
            delivery_price=delivery_price,
            instruments_by_expiry=instruments_by_expiry,
            config=config,
            cohort="weekend",
        )
        row["status"] = "matured" if row["expiry_matured"] else "pending_not_matured"
        row["scenario_issue_count"] = len(issues)
        row["scenario_rows"] = len(scenario_rows)
        for scenario in scenario_rows:
            scenario.update({
                "observation_date_bjt": row["observation_date_bjt"],
                "source_card_age_hours": row["source_card_age_hours"],
                "source_card_age_le_24h": row["source_card_age_le_24h"],
                "weekend_status": row["status"],
                "source_in_ordinary_114": row["source_in_ordinary_114"],
            })
            trades.append(scenario)
        observations.append(row)

    base_main = _main_rows(trades)
    matured_main = [row for row in base_main if row.get("is_matured") is True]
    output = {
        "observations": observations,
        "trades": trades,
        "group_summary": _summary_rows([row for row in trades if row.get("is_matured") is True], ("direction", "relation", "side", "target_width", "credit_fraction"), config),
        "age_summary": _summary_rows(matured_main, ("source_card_age_le_24h", "relation", "side"), config),
        "volatility_pairs": volatility_pairs,
        "volatility_summary": _volatility_summary(volatility_pairs),
        "main_summary": {
            "directional_vs_opposite": _summary_rows(
                [row for row in matured_main if row["relation"] in {"directional", "opposite"}],
                ("relation", "side"),
                config,
            ),
            "neutral_sides": _summary_rows(
                [row for row in matured_main if row["relation"] in {"neutral_put", "neutral_call"}],
                ("relation", "side"),
                config,
            ),
            "age_le_24h": _summary_rows(matured_main, ("source_card_age_le_24h", "relation", "side"), config),
        },
        "eligibility": {
            "observations": len(observations),
            "matured_observations": sum(1 for row in observations if row.get("status") == "matured"),
            "pending_observations": sum(1 for row in observations if row.get("status") == "pending_not_matured"),
            "source_cards_unique": len({row.get("source_card_id") for row in observations if row.get("source_card_id")}),
            "all_source_cards_in_ordinary_114": all(row.get("source_in_ordinary_114") is True for row in observations if row.get("source_card_id")),
            "age_le_24h_observations": sum(1 for row in observations if row.get("source_card_age_le_24h") is True),
            "trade_rows": len(trades),
            "matured_trade_rows": sum(1 for row in trades if row.get("is_matured") is True),
            "pending_trade_rows": sum(1 for row in trades if row.get("is_matured") is False),
            "volatility_pairs": len(volatility_pairs),
        },
    }
    return output


def _scenario_rows_for_entry(
    *,
    sample: dict[str, Any],
    entry_ms: int,
    entry_price: float,
    expiry_ms: int,
    delivery_price: float | None,
    instruments_by_expiry: dict[int, list[dict[str, Any]]],
    config: dict[str, Any],
    cohort: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    issues = []
    eligible = base._eligible_instruments(instruments_by_expiry.get(expiry_ms, []), entry_ms)
    for side, relation in base._sides_for_direction(sample["direction"]):
        short_leg = base._select_short_leg(eligible, side, entry_price)
        if short_leg is None:
            issues.append({"side": side, "reason": "missing_short_leg", "expiry_ms": expiry_ms})
            continue
        for target_width in config["target_width_usd"]:
            long_leg = base._select_long_leg(eligible, side, short_leg["strike"], float(target_width))
            if long_leg is None:
                issues.append({"side": side, "target_width": target_width, "reason": "missing_long_leg", "expiry_ms": expiry_ms})
                continue
            actual_width = base._actual_width(side, short_leg["strike"], long_leg["strike"])
            if actual_width <= 0:
                issues.append({"side": side, "target_width": target_width, "reason": "invalid_width", "expiry_ms": expiry_ms})
                continue
            spread_intrinsic_usd = None
            payout_btc = None
            if delivery_price is not None:
                spread_intrinsic_usd = base._spread_intrinsic_usd(side, short_leg["strike"], long_leg["strike"], delivery_price)
                payout_btc = spread_intrinsic_usd / delivery_price
            for credit_fraction in config["net_credit_fractions"]:
                net_credit_btc = float(credit_fraction) * actual_width / entry_price
                net_pnl_btc = None if payout_btc is None else net_credit_btc - payout_btc
                raw = {
                    "card_id": sample["card_id"],
                    "episode_id": sample.get("episode_id"),
                    "version": sample.get("version"),
                    "direction": sample["direction"],
                    "side": side,
                    "relation": relation,
                    "entry_ms": entry_ms,
                    "entry_price": entry_price,
                    "sample_card_price": sample.get("card_price"),
                    "expiry_ms": expiry_ms,
                    "delivery_date": _utc_date(expiry_ms),
                    "delivery_price": delivery_price,
                    "dte_hours": (expiry_ms - entry_ms) / HOUR_MS,
                    "dte_bucket": "weekend_55h",
                    "short_instrument": short_leg.get("instrument_name"),
                    "long_instrument": long_leg.get("instrument_name"),
                    "short_strike": short_leg["strike"],
                    "long_strike": long_leg["strike"],
                    "target_width": float(target_width),
                    "actual_width": actual_width,
                    "credit_fraction": float(credit_fraction),
                    "net_credit_btc": net_credit_btc,
                    "spread_intrinsic_usd": spread_intrinsic_usd,
                    "payout_btc": payout_btc,
                    "net_pnl_btc": net_pnl_btc,
                    "result": _result_label(net_pnl_btc),
                    "short_otm_at_expiry": _short_otm(side, short_leg["strike"], delivery_price) if delivery_price is not None else None,
                    "payout_category": base._payout_category(spread_intrinsic_usd, actual_width) if spread_intrinsic_usd is not None else None,
                    "quantity_basis": "1 BTC inverse option group",
                    "quote_basis": "synthetic net credit fraction of USD width converted at entry price; not historical quote",
                }
                rows.append(_decorate_trade(raw, config, cohort=cohort, is_matured=delivery_price is not None))
    return rows, issues


def _decorate_trade(row: dict[str, Any], config: dict[str, Any], *, cohort: str, is_matured: bool = True) -> dict[str, Any]:
    result = dict(row)
    result["cohort"] = cohort
    result["is_matured"] = is_matured
    result["entry_beijing"] = _iso_bjt(result["entry_ms"])
    result["expiry_beijing"] = _iso_bjt(result["expiry_ms"])
    result["holding_days"] = result["dte_hours"] / 24
    result["breakeven_intruded"] = (result["net_pnl_btc"] < 0) if result.get("net_pnl_btc") is not None else None
    result["breakeven_intrusion_status"] = (
        "intruded" if result.get("net_pnl_btc") is not None and result["net_pnl_btc"] < 0
        else "not_intruded" if result.get("net_pnl_btc") is not None and result["net_pnl_btc"] > 0
        else "at_breakeven" if result.get("net_pnl_btc") == 0
        else "not_matured"
    )
    margin_lambda = float(config["margin"]["lambda"])
    margin = margin_lambda * result["actual_width"] / result["entry_price"]
    result["estimated_margin_btc_lambda_1x"] = margin
    result["holding_return_on_margin_1x"] = _safe_div(result.get("net_pnl_btc"), margin)
    result["annualized_return_on_margin_1x"] = _safe_div(result.get("holding_return_on_margin_1x") * 365 if result.get("holding_return_on_margin_1x") is not None else None, result["holding_days"])
    for multiplier in config["margin"]["lambda_sensitivity_multipliers"]:
        key = _lambda_key(multiplier)
        scenario_margin = margin * float(multiplier)
        result[f"estimated_margin_btc_lambda_{key}"] = scenario_margin
        result[f"holding_return_on_margin_lambda_{key}"] = _safe_div(result.get("net_pnl_btc"), scenario_margin)
    return result


def _ordinary_card_projection(sample: dict[str, Any], row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    entry_ms = row["entry_ms"]
    session = _session_bjt(entry_ms, config["sessions_bjt"])
    ny_dt = datetime.fromtimestamp(entry_ms / 1000, tz=NY)
    ny_day = ny_dt.date().isoformat()
    ny_weekday = ny_dt.weekday()
    holidays = set(config["ny_calendar"]["study_holidays"])
    ny_is_trading_day = ny_weekday < 5 and ny_day not in holidays
    reason = "" if ny_is_trading_day else ("weekend" if ny_weekday >= 5 else "nyse_holiday")
    return {
        "card_id": sample["card_id"],
        "episode_id": sample.get("episode_id"),
        "direction": sample["direction"],
        "version": sample.get("version"),
        "event_time_ms": sample["event_time_ms"],
        "event_time_bjt": _iso_bjt(sample["event_time_ms"]),
        "entry_ms": entry_ms,
        "entry_time_bjt": _iso_bjt(entry_ms),
        "entry_date_bjt": datetime.fromtimestamp(entry_ms / 1000, tz=BJT).date().isoformat(),
        "entry_price": row["entry_price"],
        "expiry_ms": row["expiry_ms"],
        "expiry_time_bjt": _iso_bjt(row["expiry_ms"]),
        "delivery_date": row["delivery_date"],
        "dte_hours": row["dte_hours"],
        "dte_bucket": row["dte_bucket"],
        "session_bjt": session,
        "ny_date": ny_day,
        "ny_is_trading_day": ny_is_trading_day,
        "ny_market_closed_reason": reason,
    }


def _card_exclusion(sample: dict[str, Any], reason: str, row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "card_id": sample.get("card_id"),
        "episode_id": sample.get("episode_id"),
        "direction": sample.get("direction"),
        "version": sample.get("version"),
        "event_time_ms": sample.get("event_time_ms"),
        "event_time_bjt": _iso_bjt(sample["event_time_ms"]) if sample.get("event_time_ms") else None,
        "reason": reason,
        "entry_ms": row.get("entry_ms") if row else None,
        "entry_time_bjt": _iso_bjt(row["entry_ms"]) if row else None,
        "expiry_ms": row.get("expiry_ms") if row else None,
        "dte_hours": row.get("dte_hours") if row else None,
    }


def _study_saturdays(samples: list[dict[str, Any]], cutoff_ms: int) -> list[date]:
    first_day = datetime.fromtimestamp(samples[0]["event_time_ms"] / 1000, tz=BJT).date()
    cutoff_day = datetime.fromtimestamp(cutoff_ms / 1000, tz=BJT).date()
    days_until_saturday = (5 - first_day.weekday()) % 7
    current = first_day + timedelta(days=days_until_saturday)
    result = []
    while current <= cutoff_day:
        result.append(current)
        current += timedelta(days=7)
    return result


def _latest_sample_before(samples: list[dict[str, Any]], observed_at_ms: int) -> dict[str, Any] | None:
    latest = None
    for sample in samples:
        if sample["event_time_ms"] <= observed_at_ms:
            latest = sample
        else:
            break
    return latest


def _weekend_observation_base(
    saturday: date,
    observed_at_ms: int,
    entry_ms: int,
    expiry_ms: int,
    source: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "observation_date_bjt": saturday.isoformat(),
        "observed_at_ms": observed_at_ms,
        "observed_at_bjt": _iso_bjt(observed_at_ms),
        "entry_ms": entry_ms,
        "entry_time_bjt": _iso_bjt(entry_ms),
        "expiry_ms": expiry_ms,
        "expiry_time_bjt": _iso_bjt(expiry_ms),
        "expected_holding_hours": (expiry_ms - entry_ms) / HOUR_MS,
    }
    if source is None:
        return row
    age_hours = (observed_at_ms - source["event_time_ms"]) / HOUR_MS
    row.update({
        "source_card_id": source["card_id"],
        "source_episode_id": source.get("episode_id"),
        "source_direction": source.get("direction"),
        "source_version": source.get("version"),
        "source_event_ms": source.get("event_time_ms"),
        "source_event_bjt": _iso_bjt(source["event_time_ms"]),
        "source_card_price": source.get("card_price"),
        "source_card_age_hours": age_hours,
        "source_card_age_le_24h": age_hours <= config["weekend"]["age_sensitivity_hours"],
    })
    return row


def _path_window_metrics(
    kline_by_open: dict[int, dict[str, Any]],
    start_ms: int,
    end_ms: int,
    entry_price: float | None,
    prefix: str,
) -> dict[str, Any]:
    expected = max(0, int((end_ms - start_ms) // MINUTE_MS))
    if entry_price is None or entry_price <= 0:
        return {
            f"{prefix}_expected_minutes": expected,
            f"{prefix}_valid_minutes": 0,
            f"{prefix}_missing_minutes": expected,
            f"{prefix}_path_complete": False,
        }
    valid = []
    observed = 0
    for open_ms in range(start_ms, end_ms, MINUTE_MS):
        row = kline_by_open.get(open_ms)
        if row is None:
            continue
        observed += 1
        if base._path_kline_valid(row, open_ms, end_ms):
            valid.append(row)
    lows = [row["low"] for row in valid if row["low"] is not None]
    highs = [row["high"] for row in valid if row["high"] is not None]
    closes = [row["close"] for row in valid if row["close"] is not None]
    min_low = min(lows) if lows else None
    max_high = max(highs) if highs else None
    last_close = closes[-1] if closes else None
    range_pct = _safe_div(max_high - min_low, entry_price) if max_high is not None and min_low is not None else None
    return_pct = _safe_div(last_close - entry_price, entry_price) if last_close is not None else None
    return {
        f"{prefix}_expected_minutes": expected,
        f"{prefix}_observed_minutes": observed,
        f"{prefix}_valid_minutes": len(valid),
        f"{prefix}_missing_minutes": expected - len(valid),
        f"{prefix}_path_complete": len(valid) == expected,
        f"{prefix}_entry_price": entry_price,
        f"{prefix}_min_low": min_low,
        f"{prefix}_max_high": max_high,
        f"{prefix}_last_close_before_end": last_close,
        f"{prefix}_range_pct": range_pct,
        f"{prefix}_up_pct": _safe_div(max_high - entry_price, entry_price) if max_high is not None else None,
        f"{prefix}_down_pct": _safe_div(entry_price - min_low, entry_price) if min_low is not None else None,
        f"{prefix}_return_pct": return_pct,
        f"{prefix}_abs_return_pct": abs(return_pct) if return_pct is not None else None,
    }


def _summary_rows(rows: list[dict[str, Any]], keys: tuple[str, ...], config: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(key) for key in keys)].append(row)
    result = []
    for key_values, group in sorted(buckets.items(), key=lambda item: tuple("" if value is None else str(value) for value in item[0])):
        labels = {key: value for key, value in zip(keys, key_values)}
        result.append({**labels, **_metrics(group, config)})
    return result


def _metrics(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    matured = [row for row in rows if row.get("is_matured") is True and row.get("net_pnl_btc") is not None]
    n = len(matured)
    if n == 0:
        return _empty_metrics(len(rows))
    wins = [row["net_pnl_btc"] for row in matured if row["net_pnl_btc"] > 0]
    losses = [row["net_pnl_btc"] for row in matured if row["net_pnl_btc"] < 0]
    ties = [row for row in matured if row["net_pnl_btc"] == 0]
    total_credit = sum(row["net_credit_btc"] for row in matured)
    total_payout = sum(row["payout_btc"] or 0.0 for row in matured)
    total_pnl = sum(row["net_pnl_btc"] for row in matured)
    payouts = sorted((row["payout_btc"] or 0.0 for row in matured), reverse=True)
    top_n = max(1, math.ceil(n * 0.05))
    top_payout = sum(payouts[:top_n])
    avg_win = sum(wins) / len(wins) if wins else None
    avg_abs_loss = abs(sum(losses) / len(losses)) if losses else None
    metrics = {
        "n": n,
        "raw_rows": len(rows),
        "win_rate": len(wins) / n,
        "intrusion_rate": len(losses) / n,
        "tie_rate": len(ties) / n,
        "otm_rate": sum(1 for row in matured if row.get("short_otm_at_expiry") is True) / n,
        "full_width_rate": sum(1 for row in matured if row.get("payout_category") == "full_width") / n,
        "avg_win_btc": avg_win,
        "avg_abs_loss_btc": avg_abs_loss,
        "payoff_ratio": _safe_div(avg_win, avg_abs_loss),
        "total_credit_btc": total_credit,
        "total_payout_btc": total_payout,
        "total_net_pnl_btc": total_pnl,
        "tail_top_5pct_payout_btc": top_payout,
        "tail_top_5pct_payout_to_total_credit": _safe_div(top_payout, total_credit),
        "avg_dte_hours": sum(row["dte_hours"] for row in matured) / n,
        "independent_delivery_dates": len({row.get("delivery_date") for row in matured}),
        "independent_entry_dates_bjt": len({str(row.get("entry_beijing", ""))[:10] for row in matured}),
    }
    for multiplier in config["margin"]["lambda_sensitivity_multipliers"]:
        key = _lambda_key(multiplier)
        margin_days = sum(row[f"estimated_margin_btc_lambda_{key}"] * row["holding_days"] for row in matured)
        margin = sum(row[f"estimated_margin_btc_lambda_{key}"] for row in matured)
        metrics[f"total_estimated_margin_btc_lambda_{key}"] = margin
        metrics[f"capital_time_btc_days_lambda_{key}"] = margin_days
        metrics[f"scenario_apr_lambda_{key}"] = _safe_div(365 * total_pnl, margin_days)
    return metrics


def _empty_metrics(raw_rows: int) -> dict[str, Any]:
    return {
        "n": 0,
        "raw_rows": raw_rows,
        "win_rate": None,
        "intrusion_rate": None,
        "tie_rate": None,
        "otm_rate": None,
        "full_width_rate": None,
        "avg_win_btc": None,
        "avg_abs_loss_btc": None,
        "payoff_ratio": None,
        "total_credit_btc": None,
        "total_payout_btc": None,
        "total_net_pnl_btc": None,
        "tail_top_5pct_payout_btc": None,
        "tail_top_5pct_payout_to_total_credit": None,
        "avg_dte_hours": None,
        "independent_delivery_dates": 0,
        "independent_entry_dates_bjt": 0,
    }


def _volatility_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    weekend_ranges = [row["weekend_range_pct"] for row in rows if row["weekend_range_pct"] is not None]
    control_ranges = [row["control_range_pct"] for row in rows if row["control_range_pct"] is not None]
    return {
        "n": len(rows),
        "weekend_lower_range_count": sum(1 for row in rows if row["weekend_lower_range"]),
        "avg_weekend_range_pct": sum(weekend_ranges) / len(weekend_ranges) if weekend_ranges else None,
        "avg_control_range_pct": sum(control_ranges) / len(control_ranges) if control_ranges else None,
        "avg_range_ratio_weekend_to_control": sum(row["range_ratio_weekend_to_control"] for row in rows if row["range_ratio_weekend_to_control"] is not None) / len(rows),
    }


def _session_overview(
    card_index: list[dict[str, Any]],
    main_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    result = []
    for session in config["sessions_bjt"]:
        name = session["name"]
        cards = [row for row in card_index if row["session_bjt"] == name]
        rows = [row for row in main_rows if row.get("session_bjt") == name]
        metrics = _metrics(rows, config)
        result.append({
            "session_bjt": name,
            "time_range_bjt": f"{session['start']}-{session['end']}",
            "card_count": len(cards),
            "direction_counts": dict(Counter(row["direction"] for row in cards)),
            "avg_card_dte_hours": sum(row["dte_hours"] for row in cards) / len(cards) if cards else None,
            **metrics,
        })
    return result


def _with_expected_zeroes(counter: Counter, names: list[str]) -> dict[str, int]:
    result = {name: int(counter.get(name, 0)) for name in names}
    for key, value in counter.items():
        if key not in result:
            result[key] = int(value)
    return result


def _main_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row.get("target_width") == MAIN_WIDTH and row.get("credit_fraction") == MAIN_CREDIT
    ]


def _session_bjt(ms: int, sessions: list[dict[str, Any]]) -> str:
    local = datetime.fromtimestamp(ms / 1000, tz=BJT).time()
    for session in sessions:
        start = _parse_hhmm(session["start"])
        end = _parse_hhmm(session["end"])
        if start <= end:
            if start <= local < end:
                return session["name"]
        elif local >= start or local < end:
            return session["name"]
    return "未归类"


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _load_study_instruments(first_study_dir: Path) -> list[dict[str, Any]]:
    rows = list(_read_json(first_study_dir / "market" / "instruments.json"))
    live_path = first_study_dir / "market" / "cache" / "deribit" / "7af259df805b77d5_get_instruments.json"
    if live_path.exists():
        payload = _read_json(live_path)
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, list):
            existing = {row.get("instrument_name") for row in rows if isinstance(row, dict)}
            rows.extend(row for row in result if isinstance(row, dict) and row.get("instrument_name") not in existing)
    return rows


def _typed_spread_row(row: dict[str, Any]) -> dict[str, Any]:
    numeric = {
        "entry_ms", "entry_price", "sample_card_price", "expiry_ms", "delivery_price",
        "dte_hours", "short_strike", "long_strike", "target_width", "actual_width",
        "credit_fraction", "net_credit_btc", "spread_intrinsic_usd", "payout_btc", "net_pnl_btc",
    }
    booleans = {"short_otm_at_expiry"}
    result = dict(row)
    for key in numeric:
        result[key] = _num(result.get(key))
    for key in booleans:
        result[key] = _bool(result.get(key))
    result["is_matured"] = True
    return result


def _open_price(kline_by_open: dict[int, dict[str, Any]], open_ms: int) -> float | None:
    row = kline_by_open.get(open_ms)
    return float(row["open"]) if row and _positive(row.get("open")) else None


def _positive(value: Any) -> bool:
    number = _num(value)
    return number is not None and number > 0


def _result_label(net_pnl_btc: float | None) -> str:
    if net_pnl_btc is None:
        return "not_matured"
    if net_pnl_btc > 0:
        return "win"
    if net_pnl_btc < 0:
        return "loss"
    return "tie"


def _short_otm(side: str, short_strike: float, settlement: float | None) -> bool | None:
    if settlement is None:
        return None
    return settlement <= short_strike if side == SIDE_CALL else settlement >= short_strike


def _safe_div(a: Any, b: Any) -> float | None:
    a = _num(a)
    b = _num(b)
    if a is None or b is None or abs(b) <= 1e-18:
        return None
    return a / b


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"true", "1", "yes"}:
            return True
        if raw in {"false", "0", "no"}:
            return False
    return None


def _bjt_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _iso_bjt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=BJT).isoformat()


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _lambda_key(multiplier: float) -> str:
    text = f"{float(multiplier):g}".replace(".", "p")
    return f"{text}x"


def _verify_inputs(config: dict[str, Any], first_study_dir: Path) -> None:
    for relative, expected in config.get("original_inputs", {}).items():
        if not relative.startswith(".artifacts/astra-light-study-20260913/"):
            continue
        path = first_study_dir.parent / Path(relative).name
        if "/" in relative:
            tail = relative.split(".artifacts/astra-light-study-20260913/", 1)[1]
            path = first_study_dir / Path(tail)
        if not path.exists():
            raise FileNotFoundError(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Frozen input hash mismatch for {relative}: {actual} != {expected}")


def _validate_outputs(config: dict[str, Any], ordinary: dict[str, Any], weekend: dict[str, Any]) -> None:
    if ordinary["eligibility"]["ordinary_cards"] != config["ordinary_card_count"]:
        raise AssertionError(f"ordinary card count mismatch: {ordinary['eligibility']['ordinary_cards']}")
    if ordinary["eligibility"]["excluded_by_reason"].get("dte_at_most_8_hours") != config["excluded_at_most_8_hours"]:
        raise AssertionError("DTE <= 8h exclusion count mismatch")
    if ordinary["direction_counts"] != {"BULLISH": 26, "BEARISH": 40, "NEUTRAL": 48}:
        raise AssertionError(f"ordinary direction counts mismatch: {ordinary['direction_counts']}")
    expected_sessions = {row["name"]: row["expected"] for row in config["sessions_bjt"]}
    if ordinary["session_counts"] != expected_sessions:
        raise AssertionError(f"session counts mismatch: {ordinary['session_counts']} != {expected_sessions}")
    if len(ordinary["trades"]) != config["ordinary_card_count"] * 18:
        raise AssertionError("ordinary trade row count mismatch")
    weekend_cfg = config["weekend"]
    if weekend["eligibility"]["observations"] != weekend_cfg["observations"]:
        raise AssertionError("weekend observation count mismatch")
    if weekend["eligibility"]["matured_observations"] != weekend_cfg["mature_at_original_cutoff"]:
        raise AssertionError("weekend matured observation count mismatch")
    if weekend["eligibility"]["pending_observations"] != weekend_cfg["pending"]:
        raise AssertionError("weekend pending observation count mismatch")
    if weekend["eligibility"]["volatility_pairs"] != weekend_cfg["mature_at_original_cutoff"]:
        raise AssertionError("weekend volatility pair count mismatch")


def _manifest(output_dir: Path) -> dict[str, Any]:
    outputs = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "analysis_manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {
        "schema": "astra_second_study_manifest@1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outputs": outputs,
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-study-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run_second_study(args.first_study_dir.resolve(), args.config.resolve(), args.output_dir.resolve())
    print(json.dumps({
        "ordinary_cards": summary["ordinary"]["eligibility"]["ordinary_cards"],
        "ordinary_trade_rows": summary["ordinary"]["eligibility"]["trade_rows"],
        "weekend_observations": summary["weekend"]["eligibility"]["observations"],
        "weekend_matured": summary["weekend"]["eligibility"]["matured_observations"],
        "weekend_pending": summary["weekend"]["eligibility"]["pending_observations"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
