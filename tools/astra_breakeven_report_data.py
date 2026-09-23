"""Build breakeven-intrusion data for the Astra 176-signal PDF report.

This script reads the frozen light-study artifacts and creates a report-ready
projection. It does not change the historical study rows, raw audit archive,
production code, or trading logic.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "astra_breakeven_pdf_data@1.0.0"
MAIN_CREDIT_FRACTION = 0.10
SIDE_PUT = "put_credit"
SIDE_CALL = "call_credit"
SIDES = (SIDE_PUT, SIDE_CALL)
EPS = 1e-12
BJT = timezone(timedelta(hours=8))


def build_report_data(study_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = study_dir.resolve()
    config = _read_json(root / "study_config.json")
    _verify_raw_archive(root, config)

    samples = _read_csv(root / "standard_signal_samples.csv")
    spread_rows = _read_csv(root / "light_study_results.csv")
    research_overview = _read_json(root / "research_overview.json")

    _validate_samples(samples, config)
    _validate_spread_rows(spread_rows, samples, config)

    main_width = float(config.get("main_width", 2000))
    rows_by_card_side = _index_main_rows(spread_rows, main_width, MAIN_CREDIT_FRACTION)
    projections = [project_spread_row(row) for row in spread_rows]

    samples_sorted = sorted(samples, key=lambda row: (_as_ms(row.get("event_time_ms")) or 0, str(row.get("card_id") or "")))
    ledger_entries: list[dict[str, Any]] = []
    ledger_csv_rows: list[dict[str, Any]] = []
    for seq, sample in enumerate(samples_sorted, 1):
        card_id = _text(sample.get("card_id"))
        side_items: dict[str, dict[str, Any]] = {}
        for side in SIDES:
            row = rows_by_card_side.get((card_id, side))
            if row is None:
                raise ValueError(f"Missing main {side} row for card {card_id}")
            side_items[side] = project_spread_row(row)

        entry = _ledger_entry(seq, sample, side_items)
        ledger_entries.append(entry)
        ledger_csv_rows.append(_ledger_csv_row(entry))

    if len(ledger_entries) != int(config["event_count"]):
        raise ValueError("Chronological ledger does not match configured event_count")

    report_data = {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "study_dir": str(root),
        "method": _method_block(config),
        "inputs": _input_fingerprints(root),
        "overview": _overview(samples_sorted, projections, config),
        "summaries": _summaries(projections, research_overview, main_width),
        "chronological_ledger": ledger_entries,
    }
    return report_data, ledger_csv_rows


def write_outputs(report_data: dict[str, Any], ledger_rows: list[dict[str, Any]], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report_data.json"
    ledger_path = output_dir / "breakeven_ledger.csv"
    _write_json(report_path, report_data)
    _write_csv(ledger_path, ledger_rows)
    return {
        "report_data": str(report_path),
        "breakeven_ledger": str(ledger_path),
        "report_data_sha256": _sha256(report_path),
        "breakeven_ledger_sha256": _sha256(ledger_path),
    }


def project_spread_row(row: dict[str, Any]) -> dict[str, Any]:
    side = _text(row.get("side"))
    if side not in SIDES:
        raise ValueError(f"Unsupported side: {side!r}")

    entry_price = _required_float(row, "entry_price")
    delivery_price = _required_float(row, "delivery_price")
    short_strike = _required_float(row, "short_strike")
    long_strike = _required_float(row, "long_strike")
    actual_width = _required_float(row, "actual_width")
    net_credit_btc = _required_float(row, "net_credit_btc")
    payout_btc = _required_float(row, "payout_btc")
    net_pnl_btc = _required_float(row, "net_pnl_btc")
    credit_fraction = _required_float(row, "credit_fraction")
    target_width = _required_float(row, "target_width")

    breakeven_price = _near_breakeven_price(side, short_strike, net_credit_btc)
    far_reentry = actual_width / net_credit_btc if side == SIDE_CALL and net_credit_btc > 0 else None
    outcome = _pnl_outcome(net_pnl_btc)
    intrusion_status = {
        "loss": "intruded",
        "tie": "at_breakeven",
        "win": "not_intruded",
    }[outcome]

    return {
        "card_id": _text(row.get("card_id")),
        "episode_id": _text(row.get("episode_id")),
        "version": _text(row.get("version")),
        "direction": _text(row.get("direction")),
        "side": side,
        "relation": _text(row.get("relation")),
        "entry_ms": _as_ms(row.get("entry_ms")),
        "entry_utc": _ms_iso(_as_ms(row.get("entry_ms")), timezone.utc),
        "entry_bjt": _ms_iso(_as_ms(row.get("entry_ms")), BJT),
        "entry_price": entry_price,
        "sample_card_price": _optional_float(row.get("sample_card_price")),
        "expiry_ms": _as_ms(row.get("expiry_ms")),
        "expiry_utc": _ms_iso(_as_ms(row.get("expiry_ms")), timezone.utc),
        "expiry_bjt": _ms_iso(_as_ms(row.get("expiry_ms")), BJT),
        "delivery_date": _text(row.get("delivery_date")),
        "delivery_price": delivery_price,
        "dte_hours": _required_float(row, "dte_hours"),
        "dte_bucket": _text(row.get("dte_bucket")),
        "short_instrument": _text(row.get("short_instrument")),
        "long_instrument": _text(row.get("long_instrument")),
        "short_strike": short_strike,
        "long_strike": long_strike,
        "target_width": target_width,
        "actual_width": actual_width,
        "credit_fraction": credit_fraction,
        "net_credit_btc": net_credit_btc,
        "net_credit_mbtc": net_credit_btc * 1000,
        "breakeven_price": breakeven_price,
        "near_breakeven_price": breakeven_price,
        "call_far_reentry_price": far_reentry,
        "delivery_vs_breakeven_pct": _pct_distance(delivery_price, breakeven_price),
        "strike_intruded": not _bool(row.get("short_otm_at_expiry")),
        "strike_intrusion_basis": "short_strike_at_expiry",
        "breakeven_intrusion_status": intrusion_status,
        "breakeven_intrusion": intrusion_status,
        "breakeven_intruded": outcome == "loss",
        "breakeven_basis": "full_inverse_spread_payout_gt_net_credit",
        "spread_intrinsic_usd": _required_float(row, "spread_intrinsic_usd"),
        "payout_btc": payout_btc,
        "payout_mbtc": payout_btc * 1000,
        "payout_to_credit": payout_btc / net_credit_btc if net_credit_btc > 0 else None,
        "net_pnl_btc": net_pnl_btc,
        "net_pnl_mbtc": net_pnl_btc * 1000,
        "net_pnl_usd_at_delivery": net_pnl_btc * delivery_price,
        "result": outcome,
        "source_result": _text(row.get("result")),
        "payout_category": _text(row.get("payout_category")),
        "quantity_basis": _text(row.get("quantity_basis")),
        "quote_basis": _text(row.get("quote_basis")),
    }


def _ledger_entry(seq: int, sample: dict[str, Any], side_items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    event_ms = _as_ms(sample.get("event_time_ms"))
    direction = _text(sample.get("direction"))
    main_side = _main_side(direction)
    main_result = side_items.get(main_side, {}) if main_side else None
    reference_side = side_items[SIDE_PUT]
    return {
        "seq": seq,
        "card_id": _text(sample.get("card_id")),
        "episode_id": _text(sample.get("episode_id")),
        "version": _text(sample.get("version")),
        "direction": direction,
        "original_lean": _text(sample.get("original_lean")),
        "event_time_ms": event_ms,
        "event_utc": _ms_iso(event_ms, timezone.utc),
        "event_bjt": _ms_iso(event_ms, BJT),
        "signal_time_utc": _ms_iso(event_ms, timezone.utc),
        "signal_time_bjt": _ms_iso(event_ms, BJT),
        "event_date_utc": _ms_date(event_ms, timezone.utc),
        "event_date_bjt": _ms_date(event_ms, BJT),
        "entry_time_bjt": reference_side["entry_bjt"],
        "entry_time_utc": reference_side["entry_utc"],
        "expiry_time_bjt": reference_side["expiry_bjt"],
        "expiry_time_utc": reference_side["expiry_utc"],
        "entry_price": reference_side["entry_price"],
        "delivery_price": reference_side["delivery_price"],
        "dte_hours": reference_side["dte_hours"],
        "card_price": _optional_float(sample.get("card_price")),
        "card_price_unit": _text(sample.get("card_price_unit")),
        "anchor_position": _text(sample.get("anchor_position")),
        "gamma_regime": _text(sample.get("gamma_regime")),
        "board_net_gex_usd": _optional_float(sample.get("board_net_gex_usd")),
        "tmv_direction": _text(sample.get("tmv_direction")),
        "flow_direction": _text(sample.get("flow_direction")),
        "flow_alignment": _text(sample.get("flow_alignment")),
        "main_side": main_side or "neutral_no_single_main_side",
        "main_result": main_result.get("result") if main_result else "neutral_has_two_sides",
        "put_credit": side_items[SIDE_PUT],
        "call_credit": side_items[SIDE_CALL],
    }


def _ledger_csv_row(entry: dict[str, Any]) -> dict[str, Any]:
    row = {
        "seq": entry["seq"],
        "card_id": entry["card_id"],
        "episode_id": entry["episode_id"],
        "version": entry["version"],
        "direction": entry["direction"],
        "original_lean": entry["original_lean"],
        "signal_time_bjt": entry["signal_time_bjt"],
        "signal_time_utc": entry["signal_time_utc"],
        "event_bjt": entry["event_bjt"],
        "event_utc": entry["event_utc"],
        "entry_time_bjt": entry["entry_time_bjt"],
        "expiry_time_bjt": entry["expiry_time_bjt"],
        "entry_price": entry["entry_price"],
        "delivery_price": entry["delivery_price"],
        "dte_hours": entry["dte_hours"],
        "card_price": entry["card_price"],
        "card_price_unit": entry["card_price_unit"],
        "anchor_position": entry["anchor_position"],
        "gamma_regime": entry["gamma_regime"],
        "board_net_gex_usd": entry["board_net_gex_usd"],
        "tmv_direction": entry["tmv_direction"],
        "flow_direction": entry["flow_direction"],
        "flow_alignment": entry["flow_alignment"],
        "main_side": entry["main_side"],
        "main_result": entry["main_result"],
    }
    for prefix, side in (("put", SIDE_PUT), ("call", SIDE_CALL)):
        item = entry[side]
        row.update({
            f"{prefix}_relation": item["relation"],
            f"{prefix}_entry_bjt": item["entry_bjt"],
            f"{prefix}_entry_price": item["entry_price"],
            f"{prefix}_expiry_bjt": item["expiry_bjt"],
            f"{prefix}_dte_hours": item["dte_hours"],
            f"{prefix}_delivery_price": item["delivery_price"],
            f"{prefix}_short_strike": item["short_strike"],
            f"{prefix}_long_strike": item["long_strike"],
            f"{prefix}_actual_width": item["actual_width"],
            f"{prefix}_net_credit_btc": item["net_credit_btc"],
            f"{prefix}_breakeven_price": item["breakeven_price"],
            f"{prefix}_near_breakeven_price": item["near_breakeven_price"],
            f"{prefix}_call_far_reentry_price": item["call_far_reentry_price"],
            f"{prefix}_strike_intruded": item["strike_intruded"],
            f"{prefix}_breakeven_intrusion_status": item["breakeven_intrusion_status"],
            f"{prefix}_breakeven_intrusion": item["breakeven_intrusion"],
            f"{prefix}_payout_btc": item["payout_btc"],
            f"{prefix}_net_pnl_btc": item["net_pnl_btc"],
            f"{prefix}_net_pnl_mbtc": item["net_pnl_mbtc"],
            f"{prefix}_net_pnl_usd_at_delivery": item["net_pnl_usd_at_delivery"],
            f"{prefix}_result": item["result"],
        })
    return row


def _overview(samples: list[dict[str, Any]], projections: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    event_times = [_as_ms(row.get("event_time_ms")) for row in samples]
    event_times = [value for value in event_times if value is not None]
    intervals = [(b - a) / 60_000 for a, b in zip(event_times, event_times[1:]) if b >= a]
    max_interval = None
    if intervals:
        max_index = max(range(len(intervals)), key=lambda index: intervals[index])
        max_interval = {
            "minutes": intervals[max_index],
            "from_card_id": _text(samples[max_index].get("card_id")),
            "to_card_id": _text(samples[max_index + 1].get("card_id")),
            "from_bjt": _ms_iso(event_times[max_index], BJT),
            "to_bjt": _ms_iso(event_times[max_index + 1], BJT),
        }

    main_rows = [
        row for row in projections
        if _same(row.get("target_width"), config.get("main_width", 2000))
        and _same(row.get("credit_fraction"), MAIN_CREDIT_FRACTION)
    ]
    first_ms = event_times[0] if event_times else None
    last_ms = event_times[-1] if event_times else None
    return {
        "signal_count": len(samples),
        "excluded_fixed_round_count": int(config.get("fixed_excluded", 0)),
        "direction_counts": dict(Counter(_text(row.get("direction")) for row in samples)),
        "version_counts": dict(Counter(_text(row.get("version")) for row in samples)),
        "first_event_utc": _ms_iso(first_ms, timezone.utc),
        "first_event_bjt": _ms_iso(first_ms, BJT),
        "last_event_utc": _ms_iso(last_ms, timezone.utc),
        "last_event_bjt": _ms_iso(last_ms, BJT),
        "span_hours": ((last_ms - first_ms) / 3_600_000 if first_ms is not None and last_ms is not None else None),
        "span_days": ((last_ms - first_ms) / 86_400_000 if first_ms is not None and last_ms is not None else None),
        "unique_event_days_utc": len({_ms_date(ms, timezone.utc) for ms in event_times}),
        "unique_event_days_bjt": len({_ms_date(ms, BJT) for ms in event_times}),
        "unique_delivery_dates_main": len({_text(row.get("delivery_date")) for row in main_rows}),
        "first_delivery_date_main": min((_text(row.get("delivery_date")) for row in main_rows), default=""),
        "last_delivery_date_main": max((_text(row.get("delivery_date")) for row in main_rows), default=""),
        "event_interval_minutes": {
            "n": len(intervals),
            "min": min(intervals) if intervals else None,
            "median": _median(intervals),
            "mean": sum(intervals) / len(intervals) if intervals else None,
            "p90": _quantile(intervals, 0.90),
            "max": max(intervals) if intervals else None,
            "max_interval": max_interval,
        },
        "coverage_note": "The span describes sparse signal observations, not continuous trade exposure or a continuous event feed.",
    }


def _summaries(
    projections: list[dict[str, Any]],
    research_overview: dict[str, Any],
    main_width: float,
) -> dict[str, Any]:
    main_width_rows = [row for row in projections if _same(row.get("target_width"), main_width)]
    q10_rows = [row for row in main_width_rows if _same(row.get("credit_fraction"), MAIN_CREDIT_FRACTION)]
    directional = [row for row in q10_rows if row["direction"] in {"BULLISH", "BEARISH"}]
    neutral = [row for row in q10_rows if row["direction"] == "NEUTRAL"]

    fixed_side_rows = []
    for credit in sorted({_required_float(row, "credit_fraction") for row in main_width_rows}):
        pool = [row for row in main_width_rows if _same(row.get("credit_fraction"), credit) and row["direction"] in {"BULLISH", "BEARISH"}]
        fixed_side_rows.extend([
            {"benchmark": "original_direction", "credit_fraction": credit, **_metrics([row for row in pool if row["relation"] == "directional"])},
            {"benchmark": "always_put", "credit_fraction": credit, **_metrics([row for row in pool if row["side"] == SIDE_PUT])},
            {"benchmark": "always_call", "credit_fraction": credit, **_metrics([row for row in pool if row["side"] == SIDE_CALL])},
        ])

    return {
        "main_width": main_width,
        "main_credit_fraction": MAIN_CREDIT_FRACTION,
        "by_direction_side_credit": _group_metrics(main_width_rows, ("direction", "side", "credit_fraction")),
        "main_q10_by_direction_side": _group_metrics(q10_rows, ("direction", "side")),
        "directional_selected_by_credit": _group_metrics(
            [row for row in main_width_rows if row["relation"] == "directional"],
            ("credit_fraction", "relation"),
        ),
        "neutral_sides_by_credit": _group_metrics(
            [row for row in main_width_rows if row["direction"] == "NEUTRAL"],
            ("credit_fraction", "relation", "side"),
        ),
        "main_q10_directional_not_neutral": _metrics([row for row in directional if row["relation"] == "directional"]),
        "main_q10_neutral_put": _metrics([row for row in neutral if row["side"] == SIDE_PUT]),
        "main_q10_neutral_call": _metrics([row for row in neutral if row["side"] == SIDE_CALL]),
        "dte_buckets_q10": _group_metrics(q10_rows, ("dte_bucket", "direction", "relation", "side")),
        "width_sensitivity_q10_directional": _group_metrics(
            [row for row in projections if _same(row.get("credit_fraction"), MAIN_CREDIT_FRACTION) and row["relation"] == "directional"],
            ("target_width", "direction", "side"),
        ),
        "fixed_side_benchmarks": fixed_side_rows,
        "source_research_overview_excerpt": {
            "paired_day_sensitivity": research_overview.get("paired_day_sensitivity", []),
            "main_actual_width_counts": research_overview.get("main_actual_width_counts", {}),
            "source_fixed_side_benchmarks_q10": research_overview.get("fixed_side_benchmarks", []),
            "source_width_sensitivity_q10": research_overview.get("width_sensitivity", []),
        },
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = _dedupe_rows(rows)
    n = len(selected)
    if n == 0:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "win_rate": None,
            "breakeven_intrusion_rate": None,
            "strike_intrusion_rate": None,
            "avg_win_btc": None,
            "avg_abs_loss_btc": None,
            "payoff_ratio": None,
            "avg_net_pnl_btc": None,
            "total_net_pnl_btc": None,
            "total_credit_btc": None,
            "total_payout_btc": None,
            "tail_top_5pct_payout_btc": None,
            "tail_top_5pct_payout_to_total_credit": None,
            "max_loss_btc": None,
        }

    wins = [row["net_pnl_btc"] for row in selected if row["result"] == "win"]
    losses = [row["net_pnl_btc"] for row in selected if row["result"] == "loss"]
    payouts = sorted((row["payout_btc"] for row in selected), reverse=True)
    top_n = max(1, math.ceil(n * 0.05))
    top_payout = sum(payouts[:top_n])
    total_credit = sum(row["net_credit_btc"] for row in selected)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_abs_loss = abs(sum(losses) / len(losses)) if losses else None
    total_net_pnl = sum(row["net_pnl_btc"] for row in selected)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "ties": sum(1 for row in selected if row["result"] == "tie"),
        "win_rate": len(wins) / n,
        "breakeven_intrusion_rate": len(losses) / n,
        "strike_intrusion_rate": sum(1 for row in selected if row["strike_intruded"]) / n,
        "avg_win_btc": avg_win,
        "avg_abs_loss_btc": avg_abs_loss,
        "payoff_ratio": avg_win / avg_abs_loss if avg_win is not None and avg_abs_loss else None,
        "avg_net_pnl_btc": total_net_pnl / n,
        "total_net_pnl_btc": total_net_pnl,
        "total_credit_btc": total_credit,
        "total_payout_btc": sum(row["payout_btc"] for row in selected),
        "tail_top_5pct_payout_btc": top_payout,
        "tail_top_5pct_payout_to_total_credit": top_payout / total_credit if total_credit > 0 else None,
        "max_loss_btc": min(losses) if losses else None,
    }


def _group_metrics(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(key) for key in keys)].append(row)
    output = []
    for key_values, group in sorted(buckets.items(), key=lambda item: tuple("" if value is None else str(value) for value in item[0])):
        output.append({**{key: value for key, value in zip(keys, key_values)}, **_metrics(group)})
    return output


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("card_id"), row.get("side"), row.get("relation"), row.get("target_width"), row.get("credit_fraction"))
        by_key[key] = row
    return list(by_key.values())


def _index_main_rows(
    rows: list[dict[str, Any]],
    main_width: float,
    credit_fraction: float,
) -> dict[tuple[str, str], dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not _same(row.get("target_width"), main_width) or not _same(row.get("credit_fraction"), credit_fraction):
            continue
        side = _text(row.get("side"))
        if side not in SIDES:
            continue
        key = (_text(row.get("card_id")), side)
        if key in selected:
            raise ValueError(f"Duplicate main row for {key}")
        selected[key] = row
    return selected


def _validate_samples(samples: list[dict[str, Any]], config: dict[str, Any]) -> None:
    expected = int(config["event_count"])
    if len(samples) != expected:
        raise ValueError(f"Expected {expected} standard samples, found {len(samples)}")
    card_ids = [_text(row.get("card_id")) for row in samples]
    if len(set(card_ids)) != expected:
        raise ValueError("Standard samples contain duplicate card_id values")
    source_hash = _text(config.get("source_sha256"))
    mismatches = [row.get("card_id") for row in samples if _text(row.get("source_archive_sha256")) != source_hash]
    if mismatches:
        raise ValueError(f"Standard sample source hash mismatch: {mismatches[:3]}")


def _validate_spread_rows(rows: list[dict[str, Any]], samples: list[dict[str, Any]], config: dict[str, Any]) -> None:
    sample_ids = {_text(row.get("card_id")) for row in samples}
    row_ids = {_text(row.get("card_id")) for row in rows}
    extras = row_ids - sample_ids
    missing = sample_ids - row_ids
    if extras:
        raise ValueError(f"Spread rows contain unknown cards: {sorted(extras)[:3]}")
    if missing:
        raise ValueError(f"Spread rows are missing cards: {sorted(missing)[:3]}")

    widths = {float(value) for value in config.get("widths", [])}
    credits = {float(value) for value in config.get("credit_ratios", [])}
    expected = int(config["event_count"]) * len(SIDES) * len(widths) * len(credits)
    if len(rows) != expected:
        raise ValueError(f"Expected {expected} spread scenario rows, found {len(rows)}")

    main_width = float(config.get("main_width", 2000))
    main_rows = _index_main_rows(rows, main_width, MAIN_CREDIT_FRACTION)
    expected_main = int(config["event_count"]) * len(SIDES)
    if len(main_rows) != expected_main:
        raise ValueError(f"Expected {expected_main} main rows, found {len(main_rows)}")


def _verify_raw_archive(root: Path, config: dict[str, Any]) -> None:
    raw_path = root / "raw_signal_review.jsonl"
    raw = raw_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _text(config.get("source_sha256")):
        raise ValueError("Frozen raw archive sha256 does not match study_config")
    if len(raw) != int(config.get("source_bytes", -1)):
        raise ValueError("Frozen raw archive byte length does not match study_config")


def _method_block(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_basis": config.get("entry_basis"),
        "expiry_basis": config.get("expiry_basis"),
        "main_width": config.get("main_width"),
        "main_credit_fraction": MAIN_CREDIT_FRACTION,
        "sensitivity_widths": config.get("widths", []),
        "sensitivity_credit_fractions": config.get("credit_ratios", []),
        "intrusion_definition": "breakeven intrusion is true only when full inverse spread payout at delivery is greater than synthetic net credit",
        "strike_intrusion_boundary": "retained separately as short-strike intrinsic value at delivery",
        "put_near_breakeven_formula": "short_strike / (1 + net_credit_btc)",
        "call_near_breakeven_formula": "short_strike / (1 - net_credit_btc), with final outcome still decided by full two-leg inverse payout",
        "call_far_reentry_formula": "actual_width_usd / net_credit_btc; relevant only beyond the long leg because inverse call spread payout equals width / settlement",
        "quote_limitation": "synthetic credit fractions are scenario assumptions, not historical option quotes or account margin return",
    }


def _input_fingerprints(root: Path) -> dict[str, dict[str, Any]]:
    files = {
        "study_config": root / "study_config.json",
        "raw_archive": root / "raw_signal_review.jsonl",
        "standard_samples": root / "standard_signal_samples.csv",
        "light_study_results": root / "light_study_results.csv",
        "research_overview": root / "research_overview.json",
    }
    return {
        name: {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for name, path in files.items()
    }


def _near_breakeven_price(side: str, short_strike: float, net_credit_btc: float) -> float | None:
    if net_credit_btc < 0:
        return None
    if side == SIDE_PUT:
        return short_strike / (1.0 + net_credit_btc)
    if net_credit_btc >= 1:
        return None
    return short_strike / (1.0 - net_credit_btc)


def _main_side(direction: str) -> str | None:
    if direction == "BULLISH":
        return SIDE_PUT
    if direction == "BEARISH":
        return SIDE_CALL
    return None


def _pnl_outcome(net_pnl_btc: float) -> str:
    if net_pnl_btc > EPS:
        return "win"
    if net_pnl_btc < -EPS:
        return "loss"
    return "tie"


def _pct_distance(value: float, reference: float | None) -> float | None:
    if reference is None or reference == 0:
        return None
    return (value / reference - 1.0) * 100


def _same(a: Any, b: Any) -> bool:
    left = _optional_float(a)
    right = _optional_float(b)
    return left is not None and right is not None and abs(left - right) <= EPS


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - pos) + ordered[upper] * (pos - lower)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_float(row: dict[str, Any], key: str) -> float:
    value = _optional_float(row.get(key))
    if value is None:
        raise ValueError(f"Missing numeric field {key!r} in row {row.get('card_id')!r}")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_ms(value: Any) -> int | None:
    number = _optional_float(value)
    if number is None:
        return None
    if number > 10_000_000_000_000:
        number = number / 1000
    elif number < 10_000_000_000:
        number = number * 1000
    return int(number)


def _bool(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _ms_iso(ms: int | None, tz: timezone) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).isoformat()


def _ms_date(ms: int | None, tz: timezone) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).date().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report_data, ledger_rows = build_report_data(args.study_dir)
    written = write_outputs(report_data, ledger_rows, args.output)
    print(json.dumps({
        "schema": REPORT_SCHEMA,
        "signals": report_data["overview"]["signal_count"],
        "ledger_rows": len(ledger_rows),
        "output": written,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
