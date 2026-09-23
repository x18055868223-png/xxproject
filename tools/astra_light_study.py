"""Offline, frozen-sample research. Does not import or change production logic."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def truth(value):
    return value is True or (isinstance(value, str) and value.lower() in {"1", "true", "yes", "synthetic"})


def sign_label(value):
    value = str(value).lower().replace("-", "_")
    if value in {"bullish", "neutral_to_bullish"}:
        return 1
    if value in {"bearish", "neutral_to_bearish"}:
        return -1
    return 0


def normalize_card(record, line_number, source_hash):
    identity = record["identity"]
    market = record.get("market_context", {})
    window = record.get("signal_window", {})
    decision = record.get("decision", {})
    factors = record.get("factor_cross_section", {})
    lean = decision.get("lean")
    direction = {"BULLISH_STRONG": "BULLISH", "BULLISH_WEAK": "BULLISH",
                 "BEARISH_STRONG": "BEARISH", "BEARISH_WEAK": "BEARISH",
                 "NEUTRAL": "NEUTRAL"}.get(lean)
    if direction is None:
        raise ValueError(f"Unmapped contemporaneous lean: {lean!r}")
    event_ms = int(identity["confirmed_time_ms"])
    anchor = factors.get("anchor", {})
    price = finite(market.get("price"))
    axis = finite(anchor.get("effective_flip_point"))
    half = finite(anchor.get("band_half"))
    anchor_position = "unknown"
    # A clamped/default band is retained as a raw fact, never asserted as real space.
    if anchor.get("ready") is True and not anchor.get("band_clamped") and price and axis and half and half > 0:
        anchor_position = "above" if price > axis + half else "below" if price < axis - half else "inside"
    tmv = factors.get("tmvf", {})
    flow = factors.get("micro_flow", {})
    combined = flow.get("combined", {})
    tmv_sign = sign_label(tmv.get("direction"))
    flow_sign = sign_label(combined.get("direction")) if combined.get("data_ready") is True else 0
    alignment = ("aligned" if tmv_sign == flow_sign else "opposed") if tmv_sign and flow_sign else "neutral_or_unknown"
    ggr = factors.get("gamma_regime", {})
    gex = factors.get("gex_info", {})
    sample = {
        "sample_schema": "astra_standard_signal@1.0.0", "sample_included": True,
        "exclusion_reason": "", "card_id": identity["card_id"],
        "episode_id": identity.get("episode_id"), "source_event": identity.get("event_type"),
        "is_fixed_round": False, "event_time_ms": event_ms,
        "confirmed_at": identity.get("confirmed_at"),
        "event_date_utc": datetime.fromtimestamp(event_ms / 1000, timezone.utc).date().isoformat(),
        "symbol": identity.get("symbol"), "version": identity.get("strategy_version"),
        "direction": direction, "original_lean": lean, "direction_source": "decision.lean",
        "episode_shock_direction_not_prediction": window.get("episode_direction"),
        "card_price": price, "card_price_source": market.get("price_source"),
        "card_price_unit": market.get("quote_currency"),
        "anchor_position": anchor_position, "anchor_axis": axis, "anchor_half_width": half,
        "anchor_band_clamped": anchor.get("band_clamped"),
        "anchor_normalized_deviation": finite(anchor.get("normalized_deviation")),
        "anchor_freshness": anchor.get("freshness"),
        "gamma_regime": ggr.get("regime", "unknown"), "gamma_data_state": ggr.get("data_state"),
        "board_net_gex_usd": finite(gex.get("net_gamma_notional_usd")),
        "board_net_gex_source": "factor_cross_section.gex_info.net_gamma_notional_usd",
        "board_gex_stale": gex.get("stale"), "call_wall": finite(gex.get("call_wall")),
        "put_wall": finite(gex.get("put_wall")), "gamma_flip": finite(gex.get("flip_point")),
        "pin_strike": finite(gex.get("pin_strike")),
        "tmv_direction": tmv.get("direction"), "tmv_blend": finite(tmv.get("tmv_blend")),
        "flow_direction": combined.get("direction"), "flow_data_ready": combined.get("data_ready"),
        "flow_alignment": alignment,
        "flow_4h_cvd_norm": finite(flow.get("fast_4h", {}).get("cvd_norm")),
        "flow_12h_cvd_norm": finite(flow.get("slow_12h", {}).get("cvd_norm")),
        "funding_raw_rate": finite(factors.get("funding", {}).get("last_funding_rate")),
        "macro_raw_score": finite(factors.get("macro_pressure", {}).get("macro_score")),
        "has_native_near_term": bool(record.get("near_term_market_context")),
        "has_native_gex_time_semantics": bool(gex.get("gex_time_semantics")),
        "source_line": line_number, "source_archive_sha256": source_hash,
        "source_record_sha256": hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
        "source_integrity": record.get("integrity", {}),
        "raw_common_facts": factors,
    }
    return sample


def normalize_archive(raw, source_hash):
    unique = {}
    exclusions = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            card_id = record["identity"]["card_id"]
        except (ValueError, KeyError, TypeError):
            exclusions.append({"source_line": line_number, "reason": "malformed_record"})
            continue
        if card_id in unique:
            previous, previous_line = unique[card_id]
            if record != previous:
                raise ValueError(f"Conflicting duplicate card: {card_id}")
            exclusions.append({"source_line": previous_line, "card_id": card_id, "reason": "exact_duplicate"})
        unique[card_id] = record, line_number
    samples = []
    for card_id, (record, line_number) in unique.items():
        identity = record["identity"]
        event = identity.get("event_type")
        reason = None
        if truth(identity.get("is_synthetic")):
            reason = "synthetic"
        elif event == "FIXED_ANALYSIS_ROUND":
            reason = "fixed_round"
        elif event != "NR_REPAIR_CONFIRMED":
            reason = "not_repair_confirmation"
        elif record.get("signal_window", {}).get("nr_state") != "NR_REPAIR_CONFIRMED":
            reason = "confirmation_identity_mismatch"
        if reason:
            exclusions.append({"source_line": line_number, "card_id": card_id, "source_event": event, "reason": reason})
        else:
            samples.append(normalize_card(record, line_number, source_hash))
    samples.sort(key=lambda sample: (sample["event_time_ms"], sample["card_id"]))
    return samples, exclusions


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def research_summary(rows):
    """Small report views, never a learned filter or a change to the scenarios."""
    from astra_light_study_analysis import _metrics
    base = [r for r in rows if r["target_width"] == 2000 and r["credit_fraction"] == .1]

    def measure(group):
        result = _metrics(group)
        denominator = sum(r["actual_width"] / r["entry_price"] for r in group)
        result.update(
            delivery_days=len({r["delivery_date"] for r in group}),
            total_payout_btc=sum(r["payout_btc"] for r in group),
            sample_credit_break_even_fraction=sum(r["payout_btc"] for r in group) / denominator if denominator else None,
        )
        return result

    report = {"groups": [], "tenors": [], "factor_views": [], "width_sensitivity": [], "paired_day_sensitivity": []}
    for direction in ("BULLISH", "BEARISH", "NEUTRAL"):
        for side in ("put_credit", "call_credit"):
            for q in (.05, .1, .2):
                group = [r for r in rows if r["target_width"] == 2000 and r["credit_fraction"] == q and r["direction"] == direction and r["side"] == side]
                report["groups"].append({"direction": direction, "side": side, "credit_fraction": q, **measure(group)})
    for bucket in ("(0,4]", "(4,8]", "(8,16]", "(16,24]"):
        for relation in ("directional", "opposite", "neutral_put", "neutral_call"):
            report["tenors"].append({"dte_bucket": bucket, "relation": relation, **measure([r for r in base if r["dte_bucket"] == bucket and r["relation"] == relation])})
    for field in ("anchor_position", "gamma_regime", "flow_alignment", "version"):
        for value in sorted({str(r[field]) for r in base}):
            for direction in ("BULLISH", "BEARISH", "ALL_DIRECTIONAL"):
                group = [r for r in base if str(r[field]) == value and r["relation"] == "directional" and (direction == "ALL_DIRECTIONAL" or r["direction"] == direction)]
                report["factor_views"].append({"factor": field, "value": value, "direction": direction, **measure(group)})
    for width in (1500, 2000, 2500):
        for direction in ("BULLISH", "BEARISH", "ALL_DIRECTIONAL"):
            group = [r for r in rows if r["target_width"] == width and r["credit_fraction"] == .1 and r["relation"] == "directional" and (direction == "ALL_DIRECTIONAL" or r["direction"] == direction)]
            report["width_sensitivity"].append({"target_width": width, "direction": direction, **measure(group)})
    paired = defaultdict(dict)
    for row in base:
        if row["relation"] in {"directional", "opposite"}:
            paired[row["card_id"]][row["relation"]] = row
    for direction in ("BULLISH", "BEARISH", "ALL_DIRECTIONAL"):
        for width_selection in ("all_actual_widths", "both_exact_2000"):
            by_day = defaultdict(list)
            pair_count = 0
            for pair in paired.values():
                if set(pair) != {"directional", "opposite"}:
                    continue
                a, b = pair["directional"], pair["opposite"]
                if direction != "ALL_DIRECTIONAL" and a["direction"] != direction:
                    continue
                if width_selection == "both_exact_2000" and (a["actual_width"] != 2000 or b["actual_width"] != 2000):
                    continue
                by_day[a["delivery_date"]].append(a["net_pnl_btc"] - b["net_pnl_btc"])
                pair_count += 1
            day_means = [sum(values) / len(values) for values in by_day.values()]
            all_differences = [diff for values in by_day.values() for diff in values]
            report["paired_day_sensitivity"].append({
                "direction": direction, "width_selection": width_selection,
                "n": pair_count, "delivery_days": len(by_day),
                "equal_card_mean_difference_btc": sum(all_differences) / len(all_differences) if all_differences else None,
                "equal_delivery_day_mean_difference_btc": sum(day_means) / len(day_means) if day_means else None,
                "positive_days": sum(x > 1e-12 for x in day_means),
                "negative_days": sum(x < -1e-12 for x in day_means),
                "tie_days": sum(abs(x) <= 1e-12 for x in day_means),
            })
    report["main_actual_width_counts"] = dict(Counter(str(r["actual_width"]) for r in base))
    report["fixed_side_benchmarks"] = []
    directional_cards = [r for r in base if r["direction"] != "NEUTRAL"]
    for name in ("original_direction", "always_put", "always_call"):
        group = [r for r in directional_cards if (r["relation"] == "directional" if name == "original_direction" else r["side"] == ("put_credit" if name == "always_put" else "call_credit"))]
        report["fixed_side_benchmarks"].append({"benchmark": name, **measure(group)})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--normalize-only", action="store_true")
    args = parser.parse_args()
    root = args.study_dir.resolve()
    config = json.loads((root / "study_config.json").read_text(encoding="utf-8-sig"))
    raw = (root / "raw_signal_review.jsonl").read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != config["source_sha256"] or len(raw) != config["source_bytes"]:
        raise ValueError("Frozen raw archive does not match the study configuration")
    samples, exclusions = normalize_archive(raw, sha)
    counts = Counter(s["direction"] for s in samples)
    reasons = Counter(r["reason"] for r in exclusions)
    assert len(samples) == config["event_count"] == 176
    assert counts == {"BULLISH": 41, "BEARISH": 54, "NEUTRAL": 81}
    assert reasons["fixed_round"] == config["fixed_excluded"] == 47
    assert len({s["episode_id"] for s in samples}) == 176
    assert all(s["event_time_ms"] <= config["cutoff_ms"] for s in samples)
    assert all(s["card_price_source"] == "BINANCE_SPOT" and s["card_price_unit"] == "USDT" for s in samples)
    write_csv(root / "standard_signal_samples.csv", [{k: v for k, v in s.items() if k not in {"raw_common_facts", "source_integrity"}} for s in samples])
    with (root / "standard_signal_samples.jsonl").open("w", encoding="utf-8") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, ensure_ascii=False, allow_nan=False) + "\n")
    write_csv(root / "sample_exclusions.csv", exclusions)
    inventory = {
        "count": len(samples), "directions": dict(counts), "exclusions": dict(reasons),
        "versions": dict(Counter(s["version"] for s in samples)),
        "event_days_utc": len({s["event_date_utc"] for s in samples}),
        "anchor_positions": dict(Counter(s["anchor_position"] for s in samples)),
        "gamma_regimes": dict(Counter(s["gamma_regime"] for s in samples)),
        "flow_alignment": dict(Counter(s["flow_alignment"] for s in samples)),
        "first_event_ms": samples[0]["event_time_ms"], "last_event_ms": samples[-1]["event_time_ms"],
    }
    write_json(root / "normalization_manifest.json", inventory)
    if not args.normalize_only:
        from astra_light_study_analysis import analyze, TARGET_WIDTHS, CREDIT_FRACTIONS
        assert list(TARGET_WIDTHS) == config["widths"]
        assert list(CREDIT_FRACTIONS) == config["credit_ratios"]
        market = root / "market"
        market_manifest = json.loads((market / "data_manifest.json").read_text(encoding="utf-8"))
        for name in ("klines", "instruments", "delivery_prices"):
            filename = {"klines": "klines.csv", "instruments": "instruments.json", "delivery_prices": "delivery_prices.json"}[name]
            if hashlib.sha256((market / filename).read_bytes()).hexdigest() != market_manifest["outputs"][name]["sha256"]:
                raise ValueError(f"Market input hash mismatch: {name}")
        with (market / "klines.csv").open(encoding="utf-8-sig", newline="") as stream:
            klines = list(csv.DictReader(stream))
        instruments = json.loads((market / "instruments.json").read_text(encoding="utf-8"))
        deliveries = json.loads((market / "delivery_prices.json").read_text(encoding="utf-8"))
        results = analyze(samples, klines, instruments, deliveries, config["cutoff_ms"])
        for row in results["spread_rows"]:
            for field in ("entry_ms", "expiry_ms"):
                row[field.replace("_ms", "_beijing")] = datetime.fromtimestamp(row[field] / 1000, timezone(timedelta(hours=8))).isoformat()
        write_csv(root / "light_study_results.csv", results["spread_rows"])
        write_csv(root / "price_path_metrics.csv", results["path_rows"])
        write_csv(root / "outcome_exclusions.csv", results["exclusions"])
        write_json(root / "result_summaries.json", results["summaries"])
        overview = research_summary(results["spread_rows"])
        write_json(root / "research_overview.json", overview)
        write_csv(root / "group_results.csv", overview["groups"])
        write_csv(root / "tenor_results.csv", overview["tenors"])
        print(json.dumps({"samples": len(samples), "spread_rows": len(results["spread_rows"]), "outcome_exclusions": len(results["exclusions"])}, ensure_ascii=False))
    else:
        print(json.dumps(inventory, ensure_ascii=False))


if __name__ == "__main__":
    main()
