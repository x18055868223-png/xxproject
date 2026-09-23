#!/usr/bin/env python3
"""Audit frozen server facts for Astra joint research intake.

This tool is research-only. It reads the frozen slim server archive, extracts a
point-in-time native NR observation interface, and reports whether the archived
cards and available market facts are sufficient for the next joint-research
stage. It never changes FMZ, production review sidecars, LLM settings, or source
archives.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from astra_joint_data import HistoricalBars, entry_open_ms, ms_to_iso, normalize_kline
except Exception:  # pragma: no cover - fallback for isolated tests
    HistoricalBars = None  # type: ignore[assignment]

    def entry_open_ms(as_of_ms: int) -> int:
        return (int(as_of_ms) // 60_000) * 60_000 + 60_000

    def ms_to_iso(ms: int | None) -> str | None:
        if ms is None:
            return None
        return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).isoformat()

    def normalize_kline(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        try:
            open_ms = normalize_timestamp_ms(row.get("open_time_ms") or row.get("open_time"))
            close_ms = normalize_timestamp_ms(row.get("close_time_ms") or row.get("close_time"))
            open_price = finite(row.get("open"))
            high = finite(row.get("high"))
            low = finite(row.get("low"))
            close = finite(row.get("close"))
            volume = finite(row.get("volume"))
        except Exception:
            return None
        if None in (open_ms, close_ms, open_price, high, low, close, volume):
            return None
        if open_price <= 0 or high < low or close_ms < open_ms:
            return None
        return {
            "open_time_ms": int(open_ms),
            "open_time": int(open_ms),
            "close_time_ms": int(close_ms),
            "close_time": int(close_ms),
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
            "quote_volume": finite(row.get("quote_volume")),
            "trade_count": finite(row.get("trade_count")),
            "taker_buy_base_volume": finite(row.get("taker_buy_base_volume")),
            "taker_buy_quote_volume": finite(row.get("taker_buy_quote_volume")),
            "available_at_ms": normalize_timestamp_ms(row.get("available_at_ms"))
            if row.get("available_at_ms") is not None
            else None,
        }


ARCHIVE_AUDIT_SCHEMA = "astra_joint_archive_audit@1.0.0"
NR_OBSERVATION_SCHEMA = "astra_nr_observation_interface@1.0.0"
MINUTE_MS = 60_000
DAY_MS = 86_400_000
BJT = dt.timezone(dt.timedelta(hours=8))

SELECTED_FEATURE_KEYS = (
    "feature_schema",
    "feature_as_of_ms",
    "feature_as_of_utc",
    "ret_15",
    "ret_30",
    "ret_240",
    "ret_720",
    "ret_1440",
    "vol_15",
    "vol_30",
    "vol_240",
    "vol_720",
    "vol_1440",
    "net_flow_15",
    "net_flow_30",
    "net_flow_240",
    "vwap_deviation_15",
    "vwap_deviation_30",
    "vwap_migration_15",
    "range_position_15",
    "range_position_30",
    "range_expansion_15",
    "efficiency_15",
    "efficiency_30",
    "pressure_response_15",
    "pressure_response_30",
    "window_status",
)


def finite(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_timestamp_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not _looks_numeric(text):
            return iso_to_ms(text)
        value = text
    try:
        raw = int(float(value))
    except (TypeError, ValueError):
        return None
    while abs(raw) >= 100_000_000_000_000:
        raw //= 1000
    if 0 < abs(raw) < 10_000_000_000:
        raw *= 1000
    return raw


def _looks_numeric(text: str) -> bool:
    stripped = text.lstrip("+-")
    return stripped.replace(".", "", 1).isdigit()


def iso_to_ms(text: str) -> int | None:
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp() * 1000)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    os.replace(tmp, path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(
                json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            )
            stream.write("\n")
            count += 1
    os.replace(tmp, path)
    return count


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl_maybe_gzip(path: Path) -> Iterable[tuple[int, dict[str, Any] | None, str | None]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as stream:  # type: ignore[arg-type]
        for line_no, line in enumerate(stream, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                yield line_no, json.loads(text), None
            except json.JSONDecodeError as exc:
                yield line_no, None, str(exc)


def nested_get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def first_path(obj: dict[str, Any], paths: Iterable[str]) -> Any:
    for path in paths:
        value = nested_get(obj, path)
        if value is not None:
            return value
    return None


def row_time_ms(row: dict[str, Any]) -> int | None:
    value = first_path(
        row,
        (
            "ts_ms",
            "decision.ts_ms",
            "factor_snapshot.ts_ms",
            "original_card.identity.confirmed_time_ms",
            "original_card.analysis_round.scheduled_time_ms",
            "original_card.market_context.ts_ms",
        ),
    )
    return normalize_timestamp_ms(value)


def classify_fact_name(name: str) -> str:
    if name.startswith("signal_review"):
        return "signal_review"
    if name.startswith("decisions"):
        return "decision_log"
    if name.startswith("snapshots"):
        return "snapshot_log"
    return "other"


def load_manifest_by_file(source_dir: Path) -> dict[str, dict[str, Any]]:
    path = source_dir / "manifest.json"
    if not path.exists():
        return {}
    raw = read_json(path)
    items = raw.get("files") or raw.get("items") or [] if isinstance(raw, dict) else raw
    result: dict[str, dict[str, Any]] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("file"):
            result[str(item["file"])] = item
    return result


def load_scan_by_compact_file(source_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    path = source_dir / "scan_summary.json"
    if not path.exists():
        return [], {}
    raw = read_json(path)
    entries = raw if isinstance(raw, list) else raw.get("files", []) if isinstance(raw, dict) else []
    by_compact: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        compact = item.get("compact_file")
        if compact:
            by_compact[Path(str(compact)).name] = item
        name = item.get("name")
        if name:
            by_compact[str(name) + ".facts.jsonl.gz"] = item
    return entries, by_compact


def inspect_fact_file(
    path: Path, manifest_meta: dict[str, Any] | None, scan_meta: dict[str, Any] | None
) -> dict[str, Any]:
    keys: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    projection_schemas: Counter[str] = Counter()
    times: list[int] = []
    file_order_times: list[int] = []
    bad_lines = 0
    missing_time = 0
    for _line_no, obj, error in iter_jsonl_maybe_gzip(path):
        if error is not None or obj is None:
            bad_lines += 1
            continue
        for key in obj.keys():
            keys[key] += 1
        version = (
            obj.get("version")
            or nested_get(obj, "decision.demo_version")
            or nested_get(obj, "original_card.identity.strategy_version")
        )
        if version is not None:
            versions[str(version)] += 1
        schema = obj.get("projection_schema") or obj.get("schema_name") or nested_get(
            obj, "original_card.schema.name"
        )
        if schema is not None:
            projection_schemas[str(schema)] += 1
        stamp = row_time_ms(obj)
        if stamp is None:
            missing_time += 1
        else:
            times.append(stamp)
            file_order_times.append(stamp)
    gaps = 0
    backward = 0
    previous: int | None = None
    for stamp in file_order_times:
        if previous is not None:
            if stamp < previous:
                backward += 1
            if stamp - previous > 5 * MINUTE_MS:
                gaps += 1
        previous = stamp
    actual_sha = sha256_file(path)
    scan_compact = scan_meta.get("compact_sha256") if scan_meta else None
    return {
        "file": path.name,
        "kind": classify_fact_name(path.name),
        "bytes": path.stat().st_size,
        "sha256": actual_sha,
        "scan_raw_sha256": scan_meta.get("sha256") if scan_meta else None,
        "manifest_sha256": manifest_meta.get("sha256") if manifest_meta else None,
        "manifest_sha256_match": bool(manifest_meta and manifest_meta.get("sha256") == actual_sha),
        "scan_compact_sha256": scan_compact,
        "scan_compact_sha256_match": (scan_compact == actual_sha) if scan_compact else None,
        "rows": len(times) + missing_time,
        "json_bad_lines": bad_lines,
        "missing_time": missing_time,
        "first_ms": min(times) if times else None,
        "first_utc": ms_to_iso(min(times)) if times else None,
        "last_ms": max(times) if times else None,
        "last_utc": ms_to_iso(max(times)) if times else None,
        "gaps_over_5min_in_file_order": gaps,
        "backward_intervals_in_file_order": backward,
        "top_keys": dict(keys.most_common(30)),
        "versions": dict(sorted(versions.items())),
        "projection_schemas": dict(sorted(projection_schemas.items())),
        "scan_source_file": scan_meta.get("name") if scan_meta else None,
        "scan_raw_lines": scan_meta.get("lines") if scan_meta else None,
        "scan_raw_bad_lines": scan_meta.get("bad_lines") if scan_meta else None,
    }


def audit_fact_files(source_dir: Path) -> dict[str, Any]:
    manifest = load_manifest_by_file(source_dir)
    scan_entries, scan_by_compact = load_scan_by_compact_file(source_dir)
    facts = []
    for path in sorted(source_dir.glob("*.facts.jsonl.gz")):
        facts.append(inspect_fact_file(path, manifest.get(path.name), scan_by_compact.get(path.name)))
    kind_counts = Counter(item["kind"] for item in facts)
    scan_raw_bad_total = sum(int(item.get("scan_raw_bad_lines") or 0) for item in facts)
    scan_compact_mismatches = [
        item["file"] for item in facts if item.get("scan_compact_sha256_match") is False
    ]
    return {
        "source_dir": str(source_dir),
        "source_dir_exists": source_dir.exists(),
        "slim_bundle_tar_sha256": sha256_file(source_dir / "slim_bundle.tar")
        if (source_dir / "slim_bundle.tar").exists()
        else None,
        "manifest_sha256": sha256_file(source_dir / "manifest.json")
        if (source_dir / "manifest.json").exists()
        else None,
        "scan_summary_sha256": sha256_file(source_dir / "scan_summary.json")
        if (source_dir / "scan_summary.json").exists()
        else None,
        "scan_summary_entries": len(scan_entries),
        "manifest_entries": len(manifest),
        "fact_files": facts,
        "fact_file_count": len(facts),
        "fact_kind_counts": dict(sorted(kind_counts.items())),
        "manifest_mismatches": [
            item["file"] for item in facts if item["manifest_sha256"] and not item["manifest_sha256_match"]
        ],
        "scan_raw_bad_lines_total": scan_raw_bad_total,
        "scan_compact_sha256_mismatches": scan_compact_mismatches,
        "json_bad_line_files": [item for item in facts if item["json_bad_lines"]],
    }


def extract_nr_observations(source_dir: Path) -> list[dict[str, Any]]:
    path = source_dir / "signal_review.jsonl.facts.jsonl.gz"
    observations: list[dict[str, Any]] = []
    if not path.exists():
        return observations
    for line_no, row, error in iter_jsonl_maybe_gzip(path):
        if error or row is None:
            continue
        card = row.get("original_card") if isinstance(row.get("original_card"), dict) else {}
        if not card:
            continue
        ident = card.get("identity") if isinstance(card.get("identity"), dict) else {}
        analysis_round = card.get("analysis_round") if isinstance(card.get("analysis_round"), dict) else None
        event_type = str(ident.get("event_type") or "")
        is_synthetic = bool(ident.get("is_synthetic"))
        episode_id = str(ident.get("episode_id") or "")
        record_kind = classify_card_record(event_type, is_synthetic, analysis_round, episode_id)
        card_time = normalize_timestamp_ms(ident.get("confirmed_time_ms") or (analysis_round or {}).get("scheduled_time_ms"))
        fc = card.get("factor_cross_section") if isinstance(card.get("factor_cross_section"), dict) else {}
        decision = card.get("decision") if isinstance(card.get("decision"), dict) else {}
        signal_window = card.get("signal_window") if isinstance(card.get("signal_window"), dict) else {}
        anchor = extract_anchor_marker(fc)
        options = extract_options_marker(fc)
        expiry = next_bjt_16_expiry(card_time) if card_time is not None else {}
        dte_hours = expiry.get("dte_hours")
        normal_dte = isinstance(dte_hours, (int, float)) and dte_hours > 8.0 and dte_hours <= 24.0
        native_nr = record_kind == "native_nr_event"
        observations.append(
            {
                "schema": NR_OBSERVATION_SCHEMA,
                "source": {
                    "fact_file": path.name,
                    "fact_line": line_no,
                    "source_sha256": row.get("source_sha256"),
                    "source_record_hash": canonical_hash(card),
                    "projection_schema": row.get("projection_schema"),
                },
                "identity": {
                    "card_id": ident.get("card_id"),
                    "symbol": ident.get("symbol"),
                    "strategy_version": ident.get("strategy_version") or row.get("version"),
                    "event_type": event_type or None,
                    "episode_id": episode_id or None,
                    "is_synthetic": is_synthetic,
                    "record_kind": record_kind,
                    "confirmed_at": ident.get("confirmed_at"),
                    "confirmed_time_ms": card_time,
                    "confirmed_time_utc": ms_to_iso(card_time),
                    "confirmed_date_utc": date_label(card_time, dt.UTC),
                    "confirmed_date_bjt": date_label(card_time, BJT),
                },
                "original_signal_markers": {
                    "episode_direction": signal_window.get("episode_direction"),
                    "peak_m_die": signal_window.get("peak_m_die"),
                    "nr_state": signal_window.get("nr_state"),
                    "decision_lean": decision.get("lean"),
                    "side_hint": decision.get("side_hint"),
                    "support_label": decision.get("support_label"),
                    "trade_allowed": decision.get("trade_allowed"),
                },
                "availability_markers": {
                    "native_nr_event": native_nr,
                    "anchor": anchor,
                    "options": options,
                    "near_term_market_context": bool(card.get("near_term_market_context")),
                },
                "reference_timing": {
                    "entry_open_ms": entry_open_ms(card_time) if card_time is not None else None,
                    "entry_open_utc": ms_to_iso(entry_open_ms(card_time)) if card_time is not None else None,
                    **expiry,
                    "ordinary_round_8_24h": bool(normal_dte),
                },
                "research_role": {
                    "eligible_native_nr_interface": bool(native_nr),
                    "eligible_option_field_upper_bound_marker": bool(
                        native_nr and options.get("field_level_options_factor") and normal_dte
                    ),
                    "eligible_option_common_marker": bool(
                        native_nr and options.get("field_level_options_factor") and normal_dte
                    ),
                    "not_training_input_yet": True,
                    "note_cn": "原生NR与已有方向、Anchor、期权可用性只作为独立标记；本文件不写入主模型训练。",
                },
            }
        )
    observations.sort(key=lambda item: (item["identity"].get("confirmed_time_ms") or 0, item["source"].get("fact_line") or 0))
    return observations


def classify_card_record(
    event_type: str, is_synthetic: bool, analysis_round: dict[str, Any] | None, episode_id: str
) -> str:
    if is_synthetic:
        return "synthetic_sample"
    if analysis_round or event_type == "FIXED_ANALYSIS_ROUND" or episode_id.startswith("fixed_"):
        return "fixed_round"
    if event_type == "NR_REPAIR_CONFIRMED" and episode_id.startswith("nr_"):
        return "native_nr_event"
    if event_type == "NR_REPAIR_CONFIRMED":
        return "nr_other"
    return "other"


def extract_anchor_marker(fc: dict[str, Any]) -> dict[str, Any]:
    anchor = fc.get("anchor") if isinstance(fc.get("anchor"), dict) else {}
    ready_value = anchor.get("ready")
    ready = ready_value is True or anchor.get("score") is not None or anchor.get("normalized_deviation") is not None
    return {
        "available": bool(ready),
        "ready": ready_value if ready_value is not None else None,
        "score": finite(anchor.get("score")),
        "normalized_deviation": finite(anchor.get("normalized_deviation")),
        "acceptance_event": anchor.get("acceptance_event"),
        "freshness": anchor.get("freshness"),
        "source_ref": anchor.get("source_ref"),
    }


def extract_options_marker(fc: dict[str, Any]) -> dict[str, Any]:
    gex = fc.get("gex_info") if isinstance(fc.get("gex_info"), dict) else {}
    gamma = fc.get("gamma_regime") if isinstance(fc.get("gamma_regime"), dict) else {}
    regime = str(gamma.get("regime") or "").upper()
    gex_ready = (
        str(gex.get("availability") or "").lower() in {"ready", "live", "available", "ok"}
        or str(gex.get("data_status") or "").upper() == "OK"
        or str(gex.get("quality") or "").upper() == "OK"
    )
    gamma_ready = (
        str(gamma.get("data_state") or "").upper() == "OK"
        or (
            regime not in {"", "UNKNOWN", "MISSING", "NONE"}
            and (
                gamma.get("net_gamma_notional_usd") is not None
                or gamma.get("net_gamma_notional") is not None
                or gamma.get("flip_point") is not None
            )
        )
    )
    walls_available = gex.get("call_wall") is not None or gex.get("put_wall") is not None
    pin_available = (
        gamma.get("pin_strike") is not None
        or nested_get(gamma, "pin.pin_strike") is not None
        or gex.get("magnet_price") is not None
    )
    structure_fields_present = bool(gex or gamma)
    usable = bool(gex_ready or gamma_ready or walls_available or pin_available)
    return {
        "structure_fields_present": structure_fields_present,
        "field_level_options_factor": usable,
        "strict_contract_usable": None,
        "usable_options_factor": usable,
        # Kept for callers that already read the marker. It now means usable,
        # not merely that an UNKNOWN enum or placeholder field exists.
        "any_options_available": usable,
        "gex_ready": bool(gex_ready),
        "gamma_ready": bool(gamma_ready),
        "walls_available": bool(walls_available),
        "pin_available": bool(pin_available),
        "gex_availability": gex.get("availability"),
        "gex_data_status": gex.get("data_status"),
        "gamma_data_state": gamma.get("data_state"),
        "gamma_regime": gamma.get("regime"),
        "call_wall": finite(gex.get("call_wall")),
        "put_wall": finite(gex.get("put_wall")),
        "pin_strike": finite(gamma.get("pin_strike") or nested_get(gamma, "pin.pin_strike") or gex.get("magnet_price")),
        "net_gamma_notional_usd": finite(
            gex.get("net_gamma_notional_usd")
            if gex.get("net_gamma_notional_usd") is not None
            else gamma.get("net_gamma_notional_usd")
        ),
    }


def date_label(ms: int | None, tz: dt.tzinfo) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=tz).date().isoformat()


def next_bjt_16_expiry(card_time_ms: int | None) -> dict[str, Any]:
    if card_time_ms is None:
        return {"expiry_ms": None, "expiry_bjt": None, "expiry_date_bjt": None, "dte_hours": None}
    card_dt = dt.datetime.fromtimestamp(card_time_ms / 1000, tz=BJT)
    expiry_dt = card_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    if card_dt >= expiry_dt:
        expiry_dt = expiry_dt + dt.timedelta(days=1)
    expiry_ms = int(expiry_dt.timestamp() * 1000)
    return {
        "expiry_ms": expiry_ms,
        "expiry_utc": ms_to_iso(expiry_ms),
        "expiry_bjt": expiry_dt.isoformat(),
        "expiry_date_bjt": expiry_dt.date().isoformat(),
        "dte_hours": (expiry_ms - card_time_ms) / 3_600_000.0,
    }


def summarize_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = Counter(obs["identity"].get("record_kind") for obs in observations)
    versions = Counter(obs["identity"].get("strategy_version") for obs in observations)
    native = [obs for obs in observations if obs["identity"].get("record_kind") == "native_nr_event"]
    ordinary = [obs for obs in native if obs["reference_timing"].get("ordinary_round_8_24h")]
    option_structure = [
        obs for obs in ordinary if obs["availability_markers"]["options"].get("structure_fields_present")
    ]
    option_common = [
        obs for obs in ordinary if obs["availability_markers"]["options"].get("field_level_options_factor")
    ]
    native_dates_bjt = {obs["identity"].get("confirmed_date_bjt") for obs in native if obs["identity"].get("confirmed_date_bjt")}
    option_structure_dates_bjt = {
        obs["reference_timing"].get("expiry_date_bjt")
        for obs in option_structure
        if obs["reference_timing"].get("expiry_date_bjt")
    }
    option_dates_bjt = {
        obs["reference_timing"].get("expiry_date_bjt")
        for obs in option_common
        if obs["reference_timing"].get("expiry_date_bjt")
    }
    directions = Counter(obs["original_signal_markers"].get("decision_lean") for obs in native)
    sides = Counter(obs["original_signal_markers"].get("side_hint") for obs in native)
    return {
        "signal_review_card_count": len(observations),
        "record_kind_counts": dict(sorted(by_kind.items())),
        "strategy_versions": dict(sorted((str(k), v) for k, v in versions.items())),
        "native_nr_events": len(native),
        "native_nr_independent_bjt_dates": len(native_dates_bjt),
        "ordinary_8_24h_native_nr_events": len(ordinary),
        "option_structure_ordinary_events_upper_bound": len(option_structure),
        "option_structure_independent_expiry_dates_bjt_upper_bound": len(option_structure_dates_bjt),
        "option_common_ordinary_events": len(option_common),
        "option_common_independent_expiry_dates_bjt": len(option_dates_bjt),
        "option_common_basis": "field_level_upper_bound_without_deribit_contract_join",
        "option_common_strict_status": "not_evaluated_requires_contract_and_settlement_join",
        "option_common_min_100_delivery_day_gate": "pass" if len(option_dates_bjt) >= 100 else "insufficient_by_upper_bound",
        "decision_lean_counts_native_nr": dict(sorted((str(k), v) for k, v in directions.items())),
        "side_hint_counts_native_nr": dict(sorted((str(k), v) for k, v in sides.items())),
        "near_term_context_native_nr": sum(1 for obs in native if obs["availability_markers"].get("near_term_market_context")),
        "anchor_available_native_nr": sum(1 for obs in native if obs["availability_markers"]["anchor"].get("available")),
        "options_structure_present_native_nr": sum(
            1 for obs in native if obs["availability_markers"]["options"].get("structure_fields_present")
        ),
        "options_available_native_nr": sum(
            1 for obs in native if obs["availability_markers"]["options"].get("field_level_options_factor")
        ),
    }


def locate_market_facts_dir(source_dir: Path, override: Path | None) -> Path | None:
    if override:
        return override
    for candidate in (source_dir.parent.parent / "facts", source_dir.parent / "facts", source_dir / "facts"):
        if (candidate / "um").exists():
            return candidate
    return None


def utc_days_between(min_ms: int, max_ms: int) -> list[str]:
    start = dt.datetime.fromtimestamp(min_ms / 1000, tz=dt.UTC).date()
    end = dt.datetime.fromtimestamp(max_ms / 1000, tz=dt.UTC).date()
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return days


def choose_market_files(market_dir: Path, min_ms: int, max_ms: int) -> list[Path]:
    days = utc_days_between(min_ms - 2 * DAY_MS, max_ms)
    result: set[Path] = set()
    daily_months: set[str] = set()
    for pattern in ("????-??-??.parquet", "????-??-??.jsonl", "????-??-??.jsonl.gz"):
        for path in market_dir.glob(pattern):
            daily_months.add(path.name[:7])
    for day in days:
        daily = market_dir / f"{day}.parquet"
        daily_jsonl = market_dir / f"{day}.jsonl"
        daily_jsonlgz = market_dir / f"{day}.jsonl.gz"
        if daily.exists():
            result.add(daily)
        elif daily_jsonl.exists():
            result.add(daily_jsonl)
        elif daily_jsonlgz.exists():
            result.add(daily_jsonlgz)
        elif day[:7] in daily_months:
            # Once a month is represented by day partitions, a missing day is a
            # real coverage gap. Do not fall back to a broader monthly file that
            # may contain data outside the frozen complete-day boundary.
            continue
        else:
            monthly = market_dir / f"{day[:7]}.parquet"
            monthly_jsonl = market_dir / f"{day[:7]}.jsonl"
            monthly_jsonlgz = market_dir / f"{day[:7]}.jsonl.gz"
            if monthly.exists():
                result.add(monthly)
            elif monthly_jsonl.exists():
                result.add(monthly_jsonl)
            elif monthly_jsonlgz.exists():
                result.add(monthly_jsonlgz)
    return sorted(result)


def read_market_file(path: Path, min_ms: int, max_ms: int) -> list[dict[str, Any]]:
    if path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore[import-not-found]
        except ImportError:
            return []
        raw_rows = pq.read_table(path).to_pylist()
    else:
        raw_rows = [obj for _line, obj, err in iter_jsonl_maybe_gzip(path) if err is None and obj is not None]
    result: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = normalize_kline(raw)
        if row is None:
            continue
        close_ms = int(row["close_time_ms"])
        if close_ms < min_ms or close_ms > max_ms:
            continue
        available = normalize_timestamp_ms(raw.get("available_at_ms") if isinstance(raw, dict) else None)
        if available is not None:
            row["available_at_ms"] = available
        result.append(row)
    return result


def read_market_rows(facts_dir: Path, market: str, min_ms: int, max_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_dir = facts_dir / market
    if not market_dir.exists():
        return [], {"status": "missing_market_dir", "market_dir": str(market_dir), "files_read": []}
    files = choose_market_files(market_dir, min_ms, max_ms)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in files:
        try:
            rows.extend(read_market_file(path, min_ms - 2 * DAY_MS, max_ms))
        except Exception as exc:  # pragma: no cover - real data variance
            errors.append({"file": str(path), "error": str(exc)})
    rows.sort(key=lambda item: int(item["open_time_ms"]))
    dedup: dict[int, dict[str, Any]] = {}
    for row in rows:
        dedup[int(row["open_time_ms"])] = row
    normalized = [dedup[key] for key in sorted(dedup)]
    return normalized, {
        "status": "available" if normalized else "no_rows",
        "market_dir": str(market_dir),
        "files_read": [str(path) for path in files],
        "file_count": len(files),
        "row_count": len(normalized),
        "errors": errors,
        "first_open_ms": int(normalized[0]["open_time_ms"]) if normalized else None,
        "last_open_ms": int(normalized[-1]["open_time_ms"]) if normalized else None,
    }


def rebuild_um_features(
    observations: list[dict[str, Any]], facts_dir: Path | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    native = [
        obs
        for obs in observations
        if obs["identity"].get("record_kind") == "native_nr_event" and obs["identity"].get("confirmed_time_ms") is not None
    ]
    if not native:
        return observations, {"status": "no_native_nr_events"}
    if facts_dir is None:
        for obs in native:
            obs["um_rebuilt_features"] = {"status": "missing_facts_dir"}
        return observations, {"status": "missing_facts_dir"}
    min_ms = min(int(obs["identity"]["confirmed_time_ms"]) for obs in native) - 2 * DAY_MS
    max_ms = max(int(obs["identity"]["confirmed_time_ms"]) for obs in native)
    rows, market_summary = read_market_rows(facts_dir, "um", min_ms, max_ms)
    if HistoricalBars is None or not rows:
        for obs in native:
            obs["um_rebuilt_features"] = {"status": "unavailable", "reason": market_summary.get("status")}
        return observations, {"status": "unavailable", "market_summary": market_summary}
    close_times = [int(row["close_time_ms"]) for row in rows]
    rebuilt = 0
    window_counts: Counter[str] = Counter()
    for obs in native:
        card_ms = int(obs["identity"]["confirmed_time_ms"])
        index = bisect.bisect_right(close_times, card_ms)
        window_rows = []
        for row in rows[max(0, index - 3100) : index]:
            available = row.get("available_at_ms")
            if available is not None and int(available) > card_ms:
                continue
            window_rows.append(row)
        if not window_rows:
            obs["um_rebuilt_features"] = {"status": "no_closed_bars_before_card"}
            continue
        bars = HistoricalBars(window_rows)
        snapshot = bars.feature_snapshot(card_ms)
        selected = {key: snapshot.get(key) for key in SELECTED_FEATURE_KEYS if key in snapshot}
        entry_ms = entry_open_ms(card_ms)
        selected.update(
            {
                "status": "available"
                if any(v == "available" for v in (selected.get("window_status") or {}).values())
                else "insufficient",
                "entry_open_ms": entry_ms,
                "entry_open_utc": ms_to_iso(entry_ms),
                "entry_open_price_available": bars.open_at(entry_ms) is not None,
                "last_closed_price": bars.last_close_at_or_before(card_ms),
                "last_closed_time_ms": int(window_rows[-1]["close_time_ms"]),
                "last_closed_time_utc": ms_to_iso(int(window_rows[-1]["close_time_ms"])),
                "closed_bar_count_used": len(window_rows),
            }
        )
        obs["um_rebuilt_features"] = selected
        rebuilt += 1
        for key, value in (selected.get("window_status") or {}).items():
            window_counts[f"{key}:{value}"] += 1
    return observations, {
        "status": "available",
        "market_summary": market_summary,
        "native_nr_events": len(native),
        "rebuilt_native_nr_events": rebuilt,
        "window_status_counts": dict(sorted(window_counts.items())),
    }


def parquet_coverage(facts_dir: Path | None) -> dict[str, Any]:
    if facts_dir is None or not facts_dir.exists():
        return {"status": "missing"}
    manifest_path = facts_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else []
    by_market: dict[str, dict[str, Any]] = {}
    for market in ("spot", "um"):
        files = sorted((facts_dir / market).glob("*.parquet")) if (facts_dir / market).exists() else []
        manifest_rows = 0
        for item in manifest if isinstance(manifest, list) else []:
            if isinstance(item, dict) and str(item.get("path", "")).replace("\\", "/").startswith(f"facts/{market}/"):
                manifest_rows += int(item.get("rows") or 0)
        by_market[market] = {
            "parquet_file_count": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "manifest_rows": manifest_rows,
            "first_file": files[0].name if files else None,
            "last_file": files[-1].name if files else None,
        }
    return {
        "status": "available",
        "facts_dir": str(facts_dir),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.exists() else None,
        "markets": by_market,
    }


def write_outputs(output_dir: Path, audit: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "archive_audit_summary.json"
    report_path = output_dir / "archive_audit_report.md"
    observations_path = output_dir / "nr_observation_interface.jsonl"
    inventory_path = output_dir / "server_fact_inventory.csv"
    artifacts = {
        "summary_json": str(summary_path),
        "report_md": str(report_path),
        "nr_observation_interface_jsonl": str(observations_path),
        "server_fact_inventory_csv": str(inventory_path),
    }
    write_jsonl(observations_path, observations)
    write_csv(
        inventory_path,
        [
            {
                "file": item["file"],
                "kind": item["kind"],
                "rows": item["rows"],
                "bad_lines": item["json_bad_lines"],
                "missing_time": item["missing_time"],
                "first_utc": item["first_utc"],
                "last_utc": item["last_utc"],
                "sha256": item["sha256"],
                "scan_raw_sha256": item.get("scan_raw_sha256"),
                "scan_first_compact_sha256": item.get("scan_compact_sha256"),
                "manifest_match": item["manifest_sha256_match"],
            }
            for item in audit["server_frozen"]["fact_files"]
        ],
    )
    report_path.write_text(render_report(audit), encoding="utf-8", newline="\n")
    audit_with_artifacts = dict(audit)
    audit_with_artifacts["artifacts"] = artifacts
    write_json(summary_path, audit_with_artifacts)
    return artifacts


def render_report(audit: dict[str, Any]) -> str:
    frozen = audit["server_frozen"]
    obs = audit["signal_review_observations"]
    um = audit["um_rebuild"]
    coverage = audit["market_fact_coverage"]
    lines = [
        "# Astra 联合研究 server slim 只读审计",
        "",
        f"审计版本：`{ARCHIVE_AUDIT_SCHEMA}`",
        f"源目录：`{frozen['source_dir']}`",
        f"slim_bundle.tar：`{frozen.get('slim_bundle_tar_sha256') or '缺失'}`",
        f"compact facts：{frozen['fact_file_count']} 份；scan_summary：{frozen['scan_summary_entries']} 项；manifest：{frozen['manifest_entries']} 项。",
        "",
        "## 1. 冻结资料覆盖",
        "",
        f"- facts 类型：{json.dumps(frozen['fact_kind_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- manifest SHA 不匹配：{len(frozen['manifest_mismatches'])} 份。",
        f"- compact JSON 坏行文件：{len(frozen['json_bad_line_files'])} 份；scan_summary 原始坏行合计：{frozen.get('scan_raw_bad_lines_total', 0)} 行。",
        f"- scan_summary 中 compact_sha256 与本地 slim manifest SHA 不一致：{len(frozen.get('scan_compact_sha256_mismatches', []))} 份。这是已知派生身份差异：scan_summary 记录第一版 compact 投影，当前本地 slim 是第二版投影；原始 source SHA、第一版 compact SHA 与第二版 slim SHA 分开保留，不视为传输失败。",
        "",
        "| 类型 | 文件 | 行数 | 时间范围 | 坏行 |",
        "|---|---|---:|---|---:|",
    ]
    for item in frozen["fact_files"]:
        time_range = f"{item.get('first_utc') or '未知'} → {item.get('last_utc') or '未知'}"
        lines.append(f"| {item['kind']} | `{item['file']}` | {item['rows']} | {time_range} | {item['json_bad_lines']} |")
    lines.extend(
        [
            "",
            "## 2. 原生 NR 观察接口",
            "",
            f"- signal_review 原卡：{obs['signal_review_card_count']} 张。",
            f"- 原生 NR 接管事件：{obs['native_nr_events']} 张；固定轮：{obs['record_kind_counts'].get('fixed_round', 0)} 张；样例：{obs['record_kind_counts'].get('synthetic_sample', 0)} 张。",
            f"- 原生 NR 独立北京时间日期：{obs['native_nr_independent_bjt_dates']} 天。",
            f"- Anchor 可用：{obs['anchor_available_native_nr']} / {obs['native_nr_events']}；期权结构字段存在：{obs['options_structure_present_native_nr']} / {obs['native_nr_events']}；字段级期权因子：{obs['options_available_native_nr']} / {obs['native_nr_events']}；近端上下文原生可用：{obs['near_term_context_native_nr']} / {obs['native_nr_events']}。",
            f"- 8—24小时普通轮候选：{obs['ordinary_8_24h_native_nr_events']} 张；具备期权结构字段的上界：{obs['option_structure_ordinary_events_upper_bound']} 张；字段级期权共同样本上界：{obs['option_common_ordinary_events']} 张。",
            f"- 字段级期权共同样本独立到期日上界：{obs['option_common_independent_expiry_dates_bjt']} 天；100 天门槛：{obs['option_common_min_100_delivery_day_gate']}。严格共同样本还需要 Deribit 合约与结算 join，只会小于或等于该上界。",
            "",
            "结论：这份服务器沉淀适合做原生 NR 对照和近期影子接口检查，但如果只用期权共同样本做模型资格，独立到期日未达到 100 天时应标为资料不足，不能进入复杂模型训练验收。",
            "",
            "## 3. UM 卡前闭合分钟复用",
            "",
            f"- 行情 facts 状态：{coverage.get('status')}；UM Parquet：{coverage.get('markets', {}).get('um', {}).get('parquet_file_count', 0)} 份。",
            f"- UM 重建状态：{um.get('status')}；重建原生 NR：{um.get('rebuilt_native_nr_events', 0)} / {um.get('native_nr_events', 0)}。",
            f"- 窗口状态：{json.dumps(um.get('window_status_counts', {}), ensure_ascii=False, sort_keys=True)}",
            "",
            "重建规则：每张原生 NR 只使用该卡确认时点以前已经闭合且可得的 UM 合约分钟线；原卡方向、Anchor、期权结构只作为独立标记，不当作新事件触发。",
            "",
            "## 4. 后续可复用接口",
            "",
            "`nr_observation_interface.jsonl` 是只读候选账本。每行含原卡哈希、原生事件身份、原方向与侧别、Anchor/期权/近端可用性、普通轮 8—24 小时标记，以及可选 UM 重建特征。该文件没有收益、交割、评级或未来行情结果，可安全作为后续模型输入前的候选层。",
            "",
        ]
    )
    return "\n".join(lines)


def audit_archive(source_dir: Path, output_dir: Path | None = None, facts_dir: Path | None = None) -> dict[str, Any]:
    source_dir = Path(source_dir)
    if not source_dir.exists():
        raise FileNotFoundError(source_dir)
    server = audit_fact_files(source_dir)
    observations = extract_nr_observations(source_dir)
    facts = locate_market_facts_dir(source_dir, facts_dir)
    observations, um_summary = rebuild_um_features(observations, facts)
    obs_summary = summarize_observations(observations)
    market_coverage = parquet_coverage(facts)
    audit = {
        "schema": ARCHIVE_AUDIT_SCHEMA,
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "server_frozen": server,
        "market_fact_coverage": market_coverage,
        "signal_review_observations": obs_summary,
        "um_rebuild": um_summary,
        "boundaries": {
            "read_only_source": True,
            "production_changed": False,
            "llm_called": False,
            "fmz_changed": False,
            "outcomes_loaded": False,
            "no_2023_result_review": True,
        },
    }
    if output_dir is not None:
        audit["artifacts"] = write_outputs(Path(output_dir), audit, observations)
    return audit


def default_output_dir(source_dir: Path) -> Path:
    """Place reports beside the sealed research root when source is raw/server-frozen."""

    source_dir = Path(source_dir)
    if source_dir.name == "server-frozen" and source_dir.parent.name == "raw":
        return source_dir.parent.parent / "archives-analysis"
    return Path("archives-analysis")


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("R/raw/server-frozen"))
    parser.add_argument("--facts-dir", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Report directory. Defaults to <research-root>/archives-analysis for raw/server-frozen inputs.",
    )
    parser.add_argument("--json", action="store_true", help="Print full audit JSON instead of compact status.")
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir is not None else default_output_dir(args.source_dir)
    audit = audit_archive(args.source_dir, output_dir, args.facts_dir)
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    else:
        compact = {
            "schema": audit["schema"],
            "fact_files": audit["server_frozen"]["fact_file_count"],
            "signal_review_cards": audit["signal_review_observations"]["signal_review_card_count"],
            "native_nr_events": audit["signal_review_observations"]["native_nr_events"],
            "option_common_independent_expiry_dates_bjt": audit["signal_review_observations"][
                "option_common_independent_expiry_dates_bjt"
            ],
            "option_gate": audit["signal_review_observations"]["option_common_min_100_delivery_day_gate"],
            "um_rebuild": audit["um_rebuild"].get("status"),
            "artifacts": audit.get("artifacts"),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
