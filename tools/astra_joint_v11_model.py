"""Train Astra joint research v1.1 NR-compatible models.

The v1.1 main model is trained on fixed-clock common facts that can also be
provided by current NR cards or rebuilt from closed pre-card minutes. Event-only
shock/cooldown fields are deliberately rejected from the main feature groups.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from astra_joint_v11_inference import apply_probability_calibrator, apply_scale, clamp, finite_float, logit, predict_row, sigmoid, transform_row

ARTIFACT_SCHEMA = "astra_joint_v11_model_artifact@1.0.0"
DEFAULT_FINAL_END = "2026-08-31"
FORBIDDEN_MAIN_TOKENS = ("shock", "cooldown", "elapsed_from_shock", "since_shock", "episode_age")
REQUIRED_GEOMETRY_FEATURES = {"dte_hours", "short_distance_fraction", "width_fraction", "side_sign"}
REQUIRED_NEAR_TERM_FEATURES = {"ret_15", "ret_30", "vol_15", "vol_30", "net_flow_15", "net_flow_30"}

DEFAULT_COMMON_FEATURE_GROUPS: dict[str, list[str]] = {
    "geometry": ["dte_hours", "short_distance_fraction", "width_fraction", "side_sign"],
    "statistical": [
        "dte_hours", "short_distance_fraction", "width_fraction", "side_sign",
        "hour_sin", "hour_cos", "ret_15", "ret_30", "ret_240", "ret_720", "ret_1440",
        "vol_15", "vol_30", "vol_240", "net_flow_15", "net_flow_30", "net_flow_240",
        "side_adverse_ret_15", "side_adverse_ret_30", "side_adverse_flow_30",
    ],
    "joint": [
        "dte_hours", "short_distance_fraction", "width_fraction", "side_sign",
        "hour_sin", "hour_cos", "ret_15", "ret_30", "ret_240", "ret_720", "ret_1440",
        "vol_15", "vol_30", "vol_240", "net_flow_15", "net_flow_30", "net_flow_240",
        "side_adverse_ret_15", "side_adverse_ret_30", "side_adverse_flow_30",
        "range_position_15", "range_position_30", "vwap_deviation_15", "vwap_deviation_30",
        "pressure_response_15", "pressure_response_30", "side_pressure_response_15", "side_pressure_response_30",
    ],
}

DEFAULT_PROTOCOL: dict[str, Any] = {
    "schema": "astra_joint_v11_protocol@1.0.0",
    "clock_frequency_minutes": 30,
    "ordinary_dte_hours": {"lower_open": 8, "upper_closed": 24},
    "rolling_development_start": "2022-01-01",
    "rolling_development_end": "2025-12-31",
    "rolling_step_months": 12,
    "rolling_horizon_months": 12,
    "final_window_months": 24,
    "calibration_months": 3,
    "final_24m_end": DEFAULT_FINAL_END,
    "gam_degree": 2,
    "gam_knots": 5,
    "logistic_C": [0.1, 1.0, 10.0],
    "gamma_alpha": [0.1, 1.0, 10.0],
    "catboost_depth": [3, 4],
    "catboost_learning_rate": 0.03,
    "catboost_max_iterations": 500,
    "min_delivery_days": 100,
    "min_positive_delivery_days": 30,
    "min_tail_delivery_days_for_precise_probability": 10,
    "selection_primary": "expected_loss_mse",
}


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    group: str
    logistic_c: float = 1.0
    gamma_alpha: float = 0.1
    depth: int | None = None

    @property
    def candidate_id(self) -> str:
        if self.family == "gam":
            return f"gam__{self.group}__C{self.logistic_c:g}__A{self.gamma_alpha:g}"
        return f"catboost__{self.group}__D{self.depth}"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_value(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def digest_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_contract() -> tuple[dict[str, list[str]], dict[str, Any], dict[str, Any]]:
    try:
        import astra_joint_v11_contract as contract  # type: ignore
    except Exception as exc:
        groups, protocol = DEFAULT_COMMON_FEATURE_GROUPS, DEFAULT_PROTOCOL
        status = {"status": "fallback_defaults", "reason": f"{exc.__class__.__name__}: {exc}"}
        validate_groups(groups)
        return groups, protocol, status
    raw_groups = getattr(contract, "COMMON_FEATURE_GROUPS", None)
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise ValueError("astra_joint_v11_contract.COMMON_FEATURE_GROUPS must be a non-empty dict")
    groups = {str(k): [str(x) for x in v] for k, v in raw_groups.items()}
    protocol = dict(DEFAULT_PROTOCOL)
    raw_protocol = getattr(contract, "PROTOCOL_V11", None) or getattr(contract, "V11_PROTOCOL", None) or getattr(contract, "PROTOCOL", None)
    if isinstance(raw_protocol, dict):
        protocol.update(raw_protocol)
        rolling = raw_protocol.get("rolling_validation") if isinstance(raw_protocol.get("rolling_validation"), dict) else {}
        if rolling:
            protocol["rolling_development_start"] = f"{min(rolling.get('years', [2022]))}-01-01"
            protocol["rolling_development_end"] = f"{max(rolling.get('years', [2025]))}-12-31"
            protocol["final_window_months"] = int(rolling.get("lookback_months", protocol["final_window_months"]))
            protocol["calibration_months"] = int(rolling.get("calibration_months", protocol["calibration_months"]))
            protocol["rolling_step_months"] = int(rolling.get("step_months", protocol["rolling_step_months"]))
            protocol["rolling_horizon_months"] = int(rolling.get("horizon_months", protocol["rolling_horizon_months"]))
            protocol["final_24m_end"] = str(rolling.get("final_fit_cutoff", protocol["final_24m_end"]))
    model_layers = protocol.get("model_layers")
    if isinstance(model_layers, (list, tuple)) and model_layers:
        groups = {name: groups[name] for name in model_layers if name in groups}
    validate_groups(groups)
    return groups, protocol, {"status": "loaded", "module": "astra_joint_v11_contract"}


def validate_groups(groups: Mapping[str, Sequence[str]]) -> None:
    missing = {"geometry", "statistical", "joint"} - set(groups)
    if missing:
        raise ValueError(f"COMMON_FEATURE_GROUPS missing required groups: {sorted(missing)}")
    for group, names in groups.items():
        if not names:
            raise ValueError(f"empty feature group: {group}")
        for name in names:
            lowered = str(name).lower()
            if any(token in lowered for token in FORBIDDEN_MAIN_TOKENS):
                raise ValueError(f"event-only feature cannot enter NR main model: {group}.{name}")


def dataset_root_from_inputs(inputs: Sequence[str | Path], explicit_root: str | Path | None = None) -> Path | None:
    if explicit_root:
        return Path(explicit_root)
    if not inputs:
        return None
    parent = Path(inputs[0]).resolve().parent
    return parent.parent if parent.name == "model_input" else parent


def load_dataset_manifest(root: str | Path | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not root:
        return None, {"status": "not_requested"}
    path = Path(root) / "revision_dataset_manifest.json"
    if not path.exists():
        return None, {"status": "missing", "path": str(path)}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest, {"status": "loaded", "path": str(path), "sha256": digest_file(path)}


def apply_manifest_features(groups: Mapping[str, Sequence[str]], protocol: Mapping[str, Any], manifest: Mapping[str, Any] | None) -> tuple[dict[str, list[str]], dict[str, Any], dict[str, Any]]:
    if not manifest:
        return {str(k): [str(x) for x in v] for k, v in groups.items()}, dict(protocol), {"status": "no_dataset_manifest"}
    raw_features = manifest.get("model_features")
    if not isinstance(raw_features, dict) or not raw_features:
        return {str(k): [str(x) for x in v] for k, v in groups.items()}, dict(protocol), {"status": "manifest_without_model_features"}
    ignored = {str(name) for name in manifest.get("csv_compat_ignored_columns") or []}
    manifest_groups = {
        str(group): [str(name) for name in names if str(name) not in ignored]
        for group, names in raw_features.items()
        if isinstance(names, (list, tuple))
    }
    model_layers = protocol.get("model_layers")
    if isinstance(model_layers, (list, tuple)) and model_layers:
        manifest_groups = {str(name): manifest_groups[str(name)] for name in model_layers if str(name) in manifest_groups}
    validate_groups(manifest_groups)
    updated_protocol = dict(protocol)
    updated_protocol["dataset_effective_protocol_sha256"] = manifest.get("effective_protocol_sha256")
    updated_protocol["dataset_materialized_csv_protocol_sha256"] = manifest.get("materialized_csv_protocol_sha256")
    updated_protocol["dataset_protocol_sha256"] = manifest.get("protocol_sha256")
    updated_protocol["csv_compat_ignored_columns"] = sorted(ignored)
    return manifest_groups, updated_protocol, {
        "status": "loaded",
        "schema": manifest.get("schema"),
        "effective_protocol_sha256": manifest.get("effective_protocol_sha256"),
        "ignored_columns": sorted(ignored),
    }


def required_feature_names(names: Sequence[str]) -> list[str]:
    """Deployment qualification inputs, separate from trainable optional fields.

    Geometry plus near-term closed-minute evidence are the NR-facing minimum.
    Longer windows and mechanism fields can be genuinely absent on natural
    cards; those values follow the trained missing pattern and do not by
    themselves make a row unqualified.
    """
    required = REQUIRED_GEOMETRY_FEATURES | REQUIRED_NEAR_TERM_FEATURES
    return [str(name) for name in names if str(name) in required]


def read_rows(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        p = Path(path)
        with p.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                record = dict(row)
                record["_source_csv"] = str(p)
                rows.append(record)
    return rows


def training_field_allowlist(groups: Mapping[str, Sequence[str]]) -> set[str]:
    features = {name for names in groups.values() for name in names}
    identity = {
        "row_id", "observation_id", "event_family", "observation_kind",
        "as_of_ms", "as_of_utc", "entry_ms", "entry_utc", "expiry_ms", "expiry_utc",
        "delivery_date", "expiry_date", "side", "target_width", "actual_width",
        "entry_price", "payout_btc", "loss_normalized", "protection_breached",
        "protection_leg_breached", "short_leg_breached",
    }
    return identity | features


def read_training_rows(paths: Sequence[str | Path], groups: Mapping[str, Sequence[str]], protocol: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keep = training_field_allowlist(groups)
    primary = finite_float(protocol.get("primary_width"))
    rows: list[dict[str, Any]] = []
    stats = {"source_files": [], "raw_rows": 0, "kept_rows": 0, "skipped_non_primary_width_rows": 0}
    for path in paths:
        p = Path(path)
        file_stats = {"path": str(p), "raw_rows": 0, "kept_rows": 0, "skipped_non_primary_width_rows": 0}
        with p.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                stats["raw_rows"] += 1
                file_stats["raw_rows"] += 1
                width = finite_float(raw.get("target_width"))
                if primary is not None and width is not None and abs(width - primary) > 1e-9:
                    stats["skipped_non_primary_width_rows"] += 1
                    file_stats["skipped_non_primary_width_rows"] += 1
                    continue
                row = {key: raw.get(key, "") for key in keep if key in raw}
                row["_source_csv"] = str(p)
                rows.append(row)
                stats["kept_rows"] += 1
                file_stats["kept_rows"] += 1
        stats["source_files"].append(file_stats)
    return rows, stats


def discover_csvs(root: str | Path) -> list[Path]:
    folder = Path(root)
    if not folder.exists():
        return []
    model_input = folder / "model_input"
    if model_input.exists():
        files = sorted(model_input.glob("model_rows-*.csv"))
        if files:
            return files
    preferred, fallback = [], []
    for path in folder.rglob("*.csv"):
        target = preferred if any(t in path.name.lower() for t in ("v11", "common", "fixed", "clock", "nr")) else fallback
        target.append(path)
    return sorted(preferred or fallback)


def parse_ms(value: Any) -> int | None:
    number = finite_float(value)
    if number is not None:
        return int(number)
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def row_time(row: Mapping[str, Any]) -> int | None:
    return parse_ms(row.get("as_of_ms")) or parse_ms(row.get("entry_ms")) or parse_ms(row.get("recorded_at_ms"))


def delivery_day(row: Mapping[str, Any]) -> str | None:
    value = row.get("delivery_date") or row.get("expiry_date")
    if value:
        return str(value)[:10]
    expiry = parse_ms(row.get("expiry_ms"))
    if expiry is None:
        return None
    return datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime("%Y-%m-%d")


def canonical_side(value: Any) -> str | None:
    text = str(value or "").lower()
    if text in {"put", "put_credit", "bull_put"}:
        return "put_credit"
    if text in {"call", "call_credit", "bear_call"}:
        return "call_credit"
    return None


def target_loss(row: Mapping[str, Any]) -> float | None:
    direct = finite_float(row.get("loss_normalized"))
    if direct is not None:
        return max(0.0, direct)
    payout = finite_float(row.get("payout_btc"))
    width = finite_float(row.get("actual_width"))
    entry = finite_float(row.get("entry_price"))
    if payout is None or width is None or entry is None or width <= 0 or entry <= 0:
        return None
    return max(0.0, payout / (width / entry))


def target_payout(row: Mapping[str, Any]) -> float | None:
    direct = finite_float(row.get("payout_btc"))
    if direct is not None:
        return max(0.0, direct)
    loss = target_loss(row)
    width = finite_float(row.get("actual_width"))
    entry = finite_float(row.get("entry_price"))
    if loss is None or width is None or entry is None or width <= 0 or entry <= 0:
        return None
    return loss * (width / entry)


def bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "breached"}:
        return True
    if text in {"0", "false", "no", "n", "not_breached"}:
        return False
    return None


def protection_breached(row: Mapping[str, Any]) -> bool | None:
    direct = bool_or_none(row.get("protection_leg_breached"))
    if direct is None:
        direct = bool_or_none(row.get("protection_breached"))
    return direct


def enrich_rows(raw_rows: Iterable[dict[str, Any]], groups: Mapping[str, Sequence[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_features = sorted({name for names in groups.values() for name in names})
    rows, gaps = [], []
    for index, raw in enumerate(raw_rows):
        side = canonical_side(raw.get("side"))
        loss = target_loss(raw)
        day = delivery_day(raw)
        t = row_time(raw)
        if side is None or loss is None or day is None or t is None:
            gaps.append({"index": index, "row_id": raw.get("row_id"), "reason": "missing_required_identity_or_target"})
            continue
        row = dict(raw)
        row.update(side=side, _target_loss=loss, _target_payout_btc=target_payout(raw), _delivery_date=day, _time_ms=t)
        row["_protection_breached"] = protection_breached(raw)
        if row.get("side_sign") in (None, ""):
            row["side_sign"] = -1.0 if side == "put_credit" else 1.0
        for feature in all_features:
            row.setdefault(feature, "")
        rows.append(row)
    return rows, gaps


def unique_days(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(row["_delivery_date"]) for row in rows}


def date_weights(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row["_delivery_date"])] += 1
    day_count = max(1, len(counts))
    return [1.0 / day_count / counts[str(row["_delivery_date"])] for row in rows]


def model_sample_weights(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    weights = date_weights(rows)
    total = sum(weights)
    if total <= 0:
        return [1.0 for _ in rows]
    scale = len(rows) / total
    return [weight * scale for weight in weights]


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return datetime(year, month, min(dt.day, 28), tzinfo=timezone.utc)


def date_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def between_with_purge(
    rows: Sequence[dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    observation_end_inclusive: bool = False,
    expiry_end_inclusive: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    left, right = ms(start), ms(end)
    selected: list[dict[str, Any]] = []
    purged_expiry_cross = 0
    purged_missing_expiry = 0
    for row in rows:
        observed = int(row["_time_ms"])
        in_observation_window = left <= observed <= right if observation_end_inclusive else left <= observed < right
        if not in_observation_window:
            continue
        expiry = finite_float(row.get("expiry_ms"))
        if expiry is None:
            purged_missing_expiry += 1
            continue
        expiry_ok = expiry <= right if expiry_end_inclusive else expiry < right
        if not expiry_ok:
            purged_expiry_cross += 1
            continue
        selected.append(row)
    return selected, {"expiry_cross_rows": purged_expiry_cross, "missing_expiry_rows": purged_missing_expiry}


def purge_overlapping_delivery_dates(
    segments: Mapping[str, Sequence[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for name, rows in segments.items():
        for row in rows:
            owners[str(row["_delivery_date"])].add(name)
    overlapping_dates = {day for day, names in owners.items() if len(names) > 1}
    filtered: dict[str, list[dict[str, Any]]] = {}
    removed: dict[str, int] = {}
    for name, rows in segments.items():
        kept = [row for row in rows if str(row["_delivery_date"]) not in overlapping_dates]
        filtered[name] = kept
        removed[name] = len(rows) - len(kept)
    return filtered, {"overlapping_delivery_dates": sorted(overlapping_dates), "overlap_removed_rows": removed}


def between(rows: Sequence[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return between_with_purge(rows, start, end)[0]


def rolling_windows(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    start, end = date_utc(str(protocol["rolling_development_start"])), date_utc(str(protocol["rolling_development_end"]))
    step, horizon = int(protocol["rolling_step_months"]), int(protocol["rolling_horizon_months"])
    total, cal = int(protocol["final_window_months"]), int(protocol["calibration_months"])
    out, eval_start = [], start
    while eval_start <= end:
        eval_end = min(add_months(eval_start, horizon), add_months(end, 1))
        out.append({
            "name": f"{eval_start.date()}__{eval_end.date()}",
            "train_start": add_months(eval_start, -total),
            "cal_start": add_months(eval_start, -cal),
            "eval_start": eval_start,
            "eval_end": eval_end,
        })
        eval_start = add_months(eval_start, step)
    return out


def final_split(rows: Sequence[dict[str, Any]], final_end: str, protocol: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    end = date_utc(final_end)
    end_exclusive = end.replace(hour=23, minute=59, second=59, microsecond=999000)
    month_start = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    start = add_months(month_start, -int(protocol["final_window_months"]) + 1)
    cal_start = add_months(month_start, -int(protocol["calibration_months"]) + 1)
    fit, fit_purge = between_with_purge(rows, start, cal_start)
    cal, cal_purge = between_with_purge(rows, cal_start, end_exclusive, observation_end_inclusive=True, expiry_end_inclusive=True)
    filtered, overlap = purge_overlapping_delivery_dates({"fit": fit, "calibration": cal})
    fit, cal = filtered["fit"], filtered["calibration"]
    return fit, cal, {
        "fit_start": str(start.date()),
        "calibration_start": str(cal_start.date()),
        "window_end": final_end,
        "fit_rows": len(fit),
        "calibration_rows": len(cal),
        "purged_rows": {"fit": fit_purge, "calibration": cal_purge, **overlap},
    }


def qualification(rows: Sequence[Mapping[str, Any]], protocol: Mapping[str, Any]) -> dict[str, Any]:
    days = unique_days(rows)
    positive_days = {str(r["_delivery_date"]) for r in rows if float(r["_target_loss"]) > 0}
    tail_days = {str(r["_delivery_date"]) for r in rows if protection_breached(r) is True}
    ok = len(days) >= int(protocol["min_delivery_days"]) and len(positive_days) >= int(protocol["min_positive_delivery_days"])
    status = "available" if ok else "insufficient_training_scope"
    return {"status": status, "eligible": ok, "qualified": ok, "delivery_days": len(days), "positive_delivery_days": len(positive_days), "tail_delivery_days": len(tail_days), "row_count": len(rows)}


def feature_support(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> dict[str, Any]:
    features = []
    for name in names:
        values = [finite_float(row.get(name)) for row in rows]
        finite = [float(value) for value in values if value is not None]
        features.append({
            "name": name,
            "row_count": len(rows),
            "available_count": len(finite),
            "missing_count": len(rows) - len(finite),
            "missing_rate": (len(rows) - len(finite)) / len(rows) if rows else None,
            "min": min(finite) if finite else None,
            "max": max(finite) if finite else None,
            "mean": sum(finite) / len(finite) if finite else None,
        })
    return {"schema": "astra_joint_v11_feature_support@1.0.0", "features": features}


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    total = sum(weights)
    return None if total <= 0 or not values else sum(v * w for v, w in zip(values, weights)) / total


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    pairs = sorted(
        [(float(value), float(weight)) for value, weight in zip(values, weights) if math.isfinite(float(value)) and float(weight) > 0],
        key=lambda item: item[0],
    )
    if not pairs:
        return 0.0
    q = min(1.0, max(0.0, float(q)))
    cutoff = q * sum(weight for _, weight in pairs)
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= cutoff:
            return value
    return pairs[-1][0]


def weighted_mean_and_scale(values: Sequence[float], weights: Sequence[float]) -> tuple[float, float]:
    mean = weighted_mean(values, weights)
    if mean is None:
        return 0.0, 1.0
    variance = weighted_mean([(float(value) - mean) ** 2 for value in values], weights) or 0.0
    scale = math.sqrt(max(0.0, variance))
    if abs(scale) < 1e-12:
        scale = 1.0
    return float(mean), float(scale)


def weighted_spline_knots(values: Sequence[float], weights: Sequence[float], n_knots: int) -> list[float]:
    finite_values = sorted({float(value) for value in values if math.isfinite(float(value))})
    if len(finite_values) <= max(2, int(n_knots)):
        return finite_values
    quantiles = [index / (max(2, int(n_knots)) - 1) for index in range(max(2, int(n_knots)))]
    knots = sorted({weighted_quantile(values, weights, q) for q in quantiles})
    return knots if len(knots) >= 2 else [finite_values[0], finite_values[-1]]


def es95(values: Sequence[float], weights: Sequence[float]) -> float | None:
    pairs = sorted([(float(v), float(w)) for v, w in zip(values, weights) if math.isfinite(float(v))], key=lambda x: x[0])
    if not pairs:
        return None
    total, cutoff, running, tail = sum(w for _, w in pairs), 0.95 * sum(w for _, w in pairs), 0.0, []
    for value, weight in pairs:
        nxt = running + weight
        if nxt > cutoff:
            tail.append((value, nxt - cutoff if running < cutoff else weight))
        running = nxt
    denom = sum(w for _, w in tail)
    return sum(v * w for v, w in tail) / denom if denom > 0 else pairs[-1][0]


def fit_preprocessor(rows: Sequence[Mapping[str, Any]], names: Sequence[str], protocol: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np
    from sklearn.preprocessing import SplineTransformer

    trained = []
    runtime = []
    row_weights = np.asarray(date_weights(rows), dtype=float)
    for name in names:
        col = np.asarray([math.nan if finite_float(r.get(name)) is None else float(finite_float(r.get(name))) for r in rows], dtype=float)
        finite_mask = np.isfinite(col)
        finite = col[finite_mask]
        finite_weights = row_weights[finite_mask] if row_weights.size else np.asarray([], dtype=float)
        median = weighted_quantile(finite.tolist(), finite_weights.tolist(), 0.5) if finite.size else 0.0
        imputed = col.copy()
        imputed[~np.isfinite(imputed)] = median
        mean, scale = weighted_mean_and_scale(imputed.tolist(), row_weights.tolist())
        scaled = ((imputed - mean) / scale).reshape(-1, 1)
        finite_scaled = scaled.reshape(-1)[finite_mask]
        knot_values = weighted_spline_knots(finite_scaled.tolist(), finite_weights.tolist(), int(protocol["gam_knots"])) if finite_scaled.size else []
        unique = len(set(knot_values))
        missing_rate = weighted_mean([1.0 if not ok else 0.0 for ok in finite_mask.tolist()], row_weights.tolist())
        if unique < 2:
            trained.append({"name": name, "kind": "constant", "median": median, "mean": mean, "scale": scale, "include_missing_indicator": True, "training_missing_rate": missing_rate, "fit_weighting": "delivery_day_equal"})
            runtime.append({"name": name, "kind": "constant", "median": median, "mean": mean, "scale": scale})
            continue
        transformer = SplineTransformer(degree=int(protocol["gam_degree"]), knots=np.asarray(knot_values, dtype=float).reshape(-1, 1), extrapolation="constant", include_bias=True)
        transformer.fit(scaled)
        spline = transformer.bsplines_[0]
        trained.append({
            "name": name, "kind": "bspline", "median": median, "mean": mean, "scale": scale,
            "degree": int(spline.k), "knots": [float(x) for x in spline.t.tolist()],
            "coefficients": [[float(x) for x in row] for row in spline.c.tolist()],
            "include_missing_indicator": True,
            "training_missing_rate": missing_rate,
            "fit_weighting": "delivery_day_equal",
        })
        runtime.append({"name": name, "kind": "bspline", "median": median, "mean": mean, "scale": scale, "transformer": transformer})
    return {"schema": "astra_joint_v11_preprocessor@1.0.0", "feature_names": list(names), "fit_weighting": "delivery_day_equal", "trained_features": trained, "_runtime_features": runtime}


def matrix(rows: Sequence[Mapping[str, Any]], preprocessor: Mapping[str, Any]) -> list[list[float]]:
    runtime = preprocessor.get("_runtime_features")
    if runtime:
        import numpy as np

        blocks = []
        for feature in runtime:
            raw = np.asarray([math.nan if finite_float(r.get(feature["name"])) is None else float(finite_float(r.get(feature["name"]))) for r in rows], dtype=float)
            missing = ~np.isfinite(raw)
            imputed = raw.copy()
            imputed[missing] = float(feature.get("median", 0.0))
            scaled = ((imputed - float(feature.get("mean", 0.0))) / (float(feature.get("scale", 1.0)) or 1.0)).reshape(-1, 1)
            if feature["kind"] == "constant":
                transformed = np.ones((len(rows), 1), dtype=float)
            else:
                transformed = feature["transformer"].transform(scaled)
            blocks.append(transformed)
            blocks.append(missing.astype(float).reshape(-1, 1))
        return np.hstack(blocks) if blocks else np.zeros((len(rows), 0), dtype=float)
    return [transform_row(r, preprocessor)[0] for r in rows]


def catboost_matrix(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> list[list[float]]:
    return [[math.nan if finite_float(row.get(name)) is None else float(finite_float(row.get(name))) for name in names] for row in rows]


def strip_runtime(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_runtime(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [strip_runtime(v) for v in value]
    return value


def export_linear(kind: str, model: Any) -> dict[str, Any]:
    return {"kind": kind, "intercept": float(model.intercept_[0] if hasattr(model.intercept_, "__len__") else model.intercept_), "coefficients": [float(x) for x in model.coef_.reshape(-1).tolist()]}


def calibrate_probability(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float]) -> dict[str, Any]:
    triples = [
        (float(label), clamp(float(prob)), float(weight))
        for label, prob, weight in zip(labels, probs, weights)
        if finite_float(label) is not None and finite_float(prob) is not None and finite_float(weight) is not None and float(weight) > 0
    ]
    if not triples:
        return {"status": "unavailable", "reason": "empty_calibration"}
    cleaned_labels = [label for label, _, _ in triples]
    cleaned_probs = [prob for _, prob, _ in triples]
    cleaned_weights = [weight for _, _, weight in triples]
    obs, pred = weighted_mean(cleaned_labels, cleaned_weights), weighted_mean(cleaned_probs, cleaned_weights)
    if obs is None or pred is None or obs <= 0 or obs >= 1:
        return {"status": "unavailable", "reason": "one_class_or_empty_calibration", "observed_rate": obs, "predicted_rate": pred}

    logits = [logit(prob) for prob in cleaned_probs]

    def shifted_mean(delta: float) -> float:
        shifted = [sigmoid(base + delta) for base in logits]
        return float(weighted_mean(shifted, cleaned_weights) or 0.0)

    lower, upper = -80.0, 80.0
    for _ in range(100):
        mid = (lower + upper) / 2.0
        if shifted_mean(mid) < obs:
            lower = mid
        else:
            upper = mid
    delta = (lower + upper) / 2.0
    calibrated = shifted_mean(delta)
    return {
        "status": "available",
        "kind": "logit_delta",
        "solver": "weighted_intercept_bisection",
        "objective": "calibration_intercept_logloss",
        "logit_delta": delta,
        "observed_rate": obs,
        "predicted_rate": pred,
        "calibrated_predicted_rate": calibrated,
        "first_order_residual": calibrated - obs,
    }


def calibrate_scale(actual: Sequence[float], predicted: Sequence[float], weights: Sequence[float]) -> dict[str, Any]:
    obs, pred = weighted_mean(actual, weights), weighted_mean(predicted, weights)
    if obs is None or pred is None or pred <= 1e-12:
        return {"status": "unavailable", "reason": "empty_or_zero_prediction", "observed_mean": obs, "predicted_mean": pred}
    return {"status": "available", "kind": "multiplicative_scale", "scale": max(0.0, obs / pred), "observed_mean": obs, "predicted_mean": pred}


def raw_parts(rows: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> tuple[list[float], list[float], list[float]]:
    if model.get("model_family") == "catboost":
        return catboost_raw_parts(rows, model)
    from astra_joint_v11_inference import linear_score

    probs, severities, tails = [], [], []
    for vector in matrix(rows, model["preprocessor"]):
        clf, sev, tail = model["classifier"], model["positive_severity"], model.get("tail_head") or {}
        probs.append(sigmoid(linear_score(clf, vector)) if clf.get("kind") == "logistic" else float(clf.get("constant_probability", 0.0)))
        severities.append(math.exp(linear_score(sev, vector)) if sev.get("kind") == "gamma_log_link" else float(sev.get("constant_positive_loss", 0.0)))
        tails.append(sigmoid(linear_score(tail, vector)) if tail.get("status") == "available" and tail.get("kind") == "logistic" else float("nan"))
    return probs, severities, tails


def catboost_raw_parts(rows: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> tuple[list[float], list[float], list[float]]:
    runtime_classifier = model.get("_runtime_classifier")
    runtime_regressor = model.get("_runtime_regressor")
    runtime_tail = model.get("_runtime_tail")
    names = model.get("feature_names") or []
    if runtime_classifier is not None and runtime_regressor is not None:
        import numpy as np

        X = np.asarray(catboost_matrix(rows, names), dtype=float)
        probs = [float(x) for x in runtime_classifier.predict_proba(X)[:, 1].tolist()]
        severities = [max(0.0, float(x)) for x in runtime_regressor.predict(X).tolist()]
        tails = [float("nan")] * len(rows)
        if runtime_tail is not None:
            tails = [float(x) for x in runtime_tail.predict_proba(X)[:, 1].tolist()]
        return probs, severities, tails
    from astra_joint_v11_inference import _catboost_feature_vector, _predict_catboost_raw

    classifier = model.get("catboost_classifier")
    regressor = model.get("catboost_regressor")
    tail_head = model.get("catboost_tail") or model.get("tail_head") or {}
    probs: list[float] = []
    severities: list[float] = []
    tails: list[float] = []
    for row in rows:
        features, _ = _catboost_feature_vector(row, names)
        raw_logit, _ = _predict_catboost_raw(classifier, features)
        raw_loss, _ = _predict_catboost_raw(regressor, features)
        probs.append(sigmoid(raw_logit))
        severities.append(max(0.0, raw_loss))
        if isinstance(tail_head, dict) and tail_head.get("status") == "available":
            raw_tail, _ = _predict_catboost_raw(tail_head, features)
            tails.append(sigmoid(raw_tail))
        else:
            tails.append(float("nan"))
    return probs, severities, tails


def fast_predictions(rows: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> list[dict[str, Any]]:
    probs, severities, tails = raw_parts(rows, model)
    calibrators = model.get("calibrators") or {}
    predictions = []
    for row, raw_p, raw_s, raw_tail in zip(rows, probs, severities, tails):
        p = apply_probability_calibrator(raw_p, calibrators.get("occurrence"))
        s = apply_scale(raw_s, calibrators.get("positive_severity"))
        expected_loss = p * s
        width_btc = finite_float(row.get("actual_width"))
        entry_price = finite_float(row.get("entry_price"))
        expected_payout_btc = expected_loss * (width_btc / entry_price) if width_btc is not None and entry_price is not None and width_btc > 0 and entry_price > 0 else None
        tail_probability = None
        if math.isfinite(raw_tail):
            tail_probability = apply_probability_calibrator(raw_tail, calibrators.get("tail"))
        predictions.append(
            {
                "row_id": row.get("row_id"),
                "side": row.get("side"),
                "probability_positive": p,
                "conditional_positive_loss": s,
                "expected_loss_normalized": expected_loss,
                "expected_payout_btc": expected_payout_btc,
                "tail_probability": tail_probability,
                "breach_probability": tail_probability,
            }
        )
    return predictions


def fit_gam(fit_rows: Sequence[dict[str, Any]], cal_rows: Sequence[dict[str, Any]], names: Sequence[str], spec: CandidateSpec, protocol: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np
    from sklearn.linear_model import GammaRegressor, LogisticRegression

    qual = qualification(fit_rows, protocol)
    if qual["status"] != "available":
        return {"status": "unavailable", "reason": qual["status"], "model_id": spec.candidate_id, "model_family": "gam", "feature_group": spec.group, "eligibility": qual}
    prep = fit_preprocessor(fit_rows, names, protocol)
    X, y, w = np.asarray(matrix(fit_rows, prep), dtype=float), np.asarray([r["_target_loss"] for r in fit_rows], dtype=float), np.asarray(model_sample_weights(fit_rows), dtype=float)
    labels = (y > 0).astype(int)
    if len(set(labels.tolist())) < 2:
        classifier = {"kind": "constant_probability", "constant_probability": float(labels.mean() if labels.size else 0.0)}
    else:
        clf = LogisticRegression(C=float(spec.logistic_c), solver="lbfgs", max_iter=1000)
        clf.fit(X, labels, sample_weight=w)
        classifier = export_linear("logistic", clf)
    positive = y > 0
    if positive.sum() < 2 or float(np.std(y[positive])) < 1e-12:
        severity = {"kind": "constant_positive_loss", "constant_positive_loss": float(np.average(y[positive], weights=w[positive])) if positive.any() else 0.0}
    else:
        gamma = GammaRegressor(alpha=float(spec.gamma_alpha), max_iter=1000)
        gamma.fit(X[positive], y[positive], sample_weight=w[positive])
        severity = export_linear("gamma_log_link", gamma)
    tail_values = [protection_breached(r) for r in fit_rows]
    tail_days = {r["_delivery_date"] for r, v in zip(fit_rows, tail_values) if v is True}
    tail_indices = [index for index, value in enumerate(tail_values) if value is not None]
    tail_labels = np.asarray([1 if tail_values[index] else 0 for index in tail_indices], dtype=int)
    min_tail = int(protocol["min_tail_delivery_days_for_precise_probability"])
    if len(tail_days) >= min_tail and len(set(tail_labels.tolist())) == 2:
        tail = LogisticRegression(C=float(spec.logistic_c), solver="lbfgs", max_iter=1000)
        tail.fit(X[tail_indices], tail_labels, sample_weight=w[tail_indices])
        tail_head = export_linear("logistic", tail)
        tail_head.update(status="available", target="protection_breached")
    else:
        tail_head = {"status": "descriptive_only", "kind": "none", "reason": "insufficient_tail_scope", "tail_delivery_days": len(tail_days), "min_tail_delivery_days_for_precise_probability": min_tail}
    model = {
        "schema": "astra_joint_v11_gam@1.0.0", "status": "available", "model_id": spec.candidate_id,
        "model_family": "gam", "feature_group": spec.group,
        "required_feature_names": required_feature_names(names),
        "hyperparameters": {"logistic_C": spec.logistic_c, "gamma_alpha": spec.gamma_alpha, "degree": protocol["gam_degree"], "knots": protocol["gam_knots"]},
        "preprocessor": prep, "classifier": classifier, "positive_severity": severity, "tail_head": tail_head,
        "calibrators": {}, "eligibility": qual,
        "tail_summary": {"es95_all_normalized": es95([float(r["_target_loss"]) for r in fit_rows], date_weights(fit_rows)), "tail_delivery_days": len(tail_days)},
        "feature_support": feature_support(fit_rows, names),
        "sample_weighting": "delivery_day_equal_scaled_mean_one",
    }
    if cal_rows:
        cw = date_weights(cal_rows)
        probs, sevs, tails = raw_parts(cal_rows, model)
        cy = [float(r["_target_loss"]) for r in cal_rows]
        model["calibrators"]["occurrence"] = calibrate_probability([1.0 if v > 0 else 0.0 for v in cy], probs, cw)
        pos_actual = [v for v in cy if v > 0]
        pos_pred = [p for v, p in zip(cy, sevs) if v > 0]
        pos_w = [x for v, x in zip(cy, cw) if v > 0]
        # Keep inference invariant:
        # expected_loss_normalized == probability_positive * conditional_positive_loss.
        # Calibration is therefore applied to the occurrence and positive-severity
        # parts only, never as a third product-scale hidden from the displayed parts.
        model["calibrators"]["positive_severity"] = calibrate_scale(pos_actual, pos_pred, pos_w)
        tail_pairs = [(r, p, x) for r, p, x in zip(cal_rows, tails, cw) if protection_breached(r) is not None and math.isfinite(p)]
        if tail_pairs:
            model["calibrators"]["tail"] = calibrate_probability([1.0 if protection_breached(r) else 0.0 for r, _, _ in tail_pairs], [p for _, p, _ in tail_pairs], [x for _, _, x in tail_pairs])
    return model


def internal_catboost_split(rows: Sequence[dict[str, Any]], protocol: Mapping[str, Any]) -> tuple[list[int], list[int]]:
    by_day: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_day[str(row["_delivery_date"])].append(index)
    days = sorted(by_day)
    if len(days) < max(20, int(protocol["min_delivery_days"]) // 2):
        return list(range(len(rows))), []
    eval_days_count = max(1, min(len(days) // 5, len(days) - max(1, int(protocol["min_delivery_days"]) // 2)))
    if eval_days_count <= 0:
        return list(range(len(rows))), []
    eval_days = set(days[-eval_days_count:])
    train_idx = [i for day in days if day not in eval_days for i in by_day[day]]
    eval_idx = [i for day in days if day in eval_days for i in by_day[day]]
    train_targets = [float(rows[i]["_target_loss"]) for i in train_idx]
    eval_targets = [float(rows[i]["_target_loss"]) for i in eval_idx]
    if not train_idx or not eval_idx or len({v > 0 for v in train_targets}) < 2 or not any(v > 0 for v in eval_targets):
        return list(range(len(rows))), []
    return train_idx, eval_idx


def selected_iterations(trained_model: Any, fallback: int) -> int:
    try:
        best = trained_model.get_best_iteration()
    except Exception:
        best = None
    if best is not None and int(best) >= 0:
        return max(1, int(best) + 1)
    count = getattr(trained_model, "tree_count_", None)
    return max(1, int(count if count is not None else fallback))


def export_catboost_model(native_model: Any, names: Sequence[str], role: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="astra_v11_catboost_") as tmp:
        path = Path(tmp) / f"{role}.json"
        native_model.save_model(str(path), format="json")
        payload = json.loads(path.read_text(encoding="utf-8"))
    return portable_catboost_from_json(payload, names, role)


def portable_catboost_from_json(payload: Mapping[str, Any], names: Sequence[str], role: str) -> dict[str, Any]:
    features_info = payload.get("features_info") or {}
    nan_treatments: dict[int, str] = {}
    for feature in features_info.get("float_features") or []:
        if isinstance(feature, dict):
            flat_index = feature.get("flat_feature_index", feature.get("feature_index"))
            if flat_index is not None:
                nan_treatments[int(flat_index)] = str(feature.get("nan_value_treatment") or "")
    trees = []
    for tree in payload.get("oblivious_trees") or []:
        splits = []
        for split in tree.get("splits") or []:
            if str(split.get("split_type") or "") != "FloatFeature":
                raise ValueError(f"unsupported CatBoost split type: {split.get('split_type')}")
            raw_index = split.get("float_feature_index", split.get("flat_feature_index"))
            if raw_index is None:
                raise ValueError("CatBoost float split lacks feature index")
            feature_index = int(raw_index)
            if feature_index < 0 or feature_index >= len(names):
                raise ValueError(f"CatBoost split feature index out of range: {feature_index}")
            treatment = str(split.get("nan_value_treatment") or nan_treatments.get(feature_index) or "")
            splits.append({
                "feature_index": feature_index,
                "feature_name": names[feature_index],
                "border": float(split["border"]),
                "missing_goes_right": treatment in {"AsTrue", "Max"},
                "nan_value_treatment": treatment or None,
            })
        trees.append({"splits": splits, "leaf_values": scalar_leaf_values(tree.get("leaf_values"))})
    scale, bias = catboost_scale_and_bias(payload)
    return {"format": "catboost_oblivious_trees_json@1.0.0", "role": role, "prediction_type": "raw_formula", "scale": scale, "bias": bias, "oblivious_trees": trees, "tree_count": len(trees), "feature_count": len(names)}


def scalar_leaf_values(raw_values: object) -> list[float]:
    if not isinstance(raw_values, list):
        raise ValueError("CatBoost tree leaf_values must be a list")
    if all(isinstance(value, (int, float)) for value in raw_values):
        return [float(value) for value in raw_values]
    out = []
    for value in raw_values:
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (int, float)):
            out.append(float(value[0]))
        elif isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
            out.append(float(value[1]) - float(value[0]))
        else:
            raise ValueError("CatBoost leaf values are not scalar binary leaves")
    return out


def catboost_scale_and_bias(payload: Mapping[str, Any]) -> tuple[float, float]:
    scale_and_bias = payload.get("scale_and_bias")
    if isinstance(scale_and_bias, list) and scale_and_bias:
        scale = float(scale_and_bias[0])
        bias = 0.0
        if len(scale_and_bias) > 1:
            raw = scale_and_bias[1]
            if isinstance(raw, list) and raw:
                bias = float(raw[0])
            elif isinstance(raw, (int, float)):
                bias = float(raw)
        return scale, bias
    return 1.0, 0.0


def fit_catboost(fit_rows: Sequence[dict[str, Any]], cal_rows: Sequence[dict[str, Any]], names: Sequence[str], spec: CandidateSpec, protocol: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np
    from catboost import CatBoostClassifier, CatBoostRegressor

    qual = qualification(fit_rows, protocol)
    if qual["status"] != "available":
        return {"status": "unavailable", "reason": qual["status"], "model_id": spec.candidate_id, "model_family": "catboost", "feature_group": spec.group, "eligibility": qual}
    X = np.asarray(catboost_matrix(fit_rows, names), dtype=float)
    y = np.asarray([float(r["_target_loss"]) for r in fit_rows], dtype=float)
    w = np.asarray(model_sample_weights(fit_rows), dtype=float)
    labels = (y > 0).astype(int)
    if len(set(labels.tolist())) < 2:
        return {"status": "unavailable", "reason": "one_class_occurrence", "model_id": spec.candidate_id, "model_family": "catboost", "feature_group": spec.group, "eligibility": qual}
    train_idx, eval_idx = internal_catboost_split(fit_rows, protocol)
    max_iter = int(protocol["catboost_max_iterations"])
    common = dict(depth=int(spec.depth or 3), learning_rate=float(protocol["catboost_learning_rate"]), iterations=max_iter, random_seed=int(protocol.get("seed", 20260915)), verbose=False, allow_writing_files=False, thread_count=6)
    classifier_probe = CatBoostClassifier(loss_function="Logloss", **common)
    if eval_idx:
        classifier_probe.fit(X[train_idx], labels[train_idx], sample_weight=w[train_idx], eval_set=(X[eval_idx], labels[eval_idx]), use_best_model=True)
    else:
        classifier_probe.fit(X, labels, sample_weight=w)
    classifier_iterations = selected_iterations(classifier_probe, max_iter)
    positive = y > 0
    if positive.sum() < 2:
        return {"status": "unavailable", "reason": "insufficient_positive_rows", "model_id": spec.candidate_id, "model_family": "catboost", "feature_group": spec.group, "eligibility": qual}
    positive_idx = [i for i in range(len(fit_rows)) if positive[i]]
    probe_positive_train = [i for i in train_idx if positive[i]]
    probe_positive_eval = [i for i in eval_idx if positive[i]]
    regressor_probe = CatBoostRegressor(loss_function="RMSE", **common)
    if probe_positive_eval and len(probe_positive_train) >= 2:
        regressor_probe.fit(X[probe_positive_train], y[probe_positive_train], sample_weight=w[probe_positive_train], eval_set=(X[probe_positive_eval], y[probe_positive_eval]), use_best_model=True)
    else:
        regressor_probe.fit(X[positive_idx], y[positive_idx], sample_weight=w[positive_idx])
    regressor_iterations = selected_iterations(regressor_probe, max_iter)
    classifier = CatBoostClassifier(loss_function="Logloss", **{**common, "iterations": classifier_iterations})
    classifier.fit(X, labels, sample_weight=w)
    regressor = CatBoostRegressor(loss_function="RMSE", **{**common, "iterations": regressor_iterations})
    regressor.fit(X[positive], y[positive], sample_weight=w[positive])
    tail_values = [protection_breached(r) for r in fit_rows]
    tail_days = {r["_delivery_date"] for r, v in zip(fit_rows, tail_values) if v is True}
    tail_indices = [index for index, value in enumerate(tail_values) if value is not None]
    tail_index_set = set(tail_indices)
    tail_labels = np.asarray([1 if tail_values[index] else 0 for index in tail_indices], dtype=int)
    tail_native = None
    min_tail = int(protocol["min_tail_delivery_days_for_precise_probability"])
    if len(tail_days) >= min_tail and len(set(tail_labels.tolist())) == 2:
        tail_probe = CatBoostClassifier(loss_function="Logloss", **common)
        tail_train_idx = [index for index in train_idx if index in tail_index_set]
        tail_eval_idx = [index for index in eval_idx if index in tail_index_set]
        if tail_eval_idx and len({int(tail_values[i]) for i in tail_eval_idx}) == 2 and len({int(tail_values[i]) for i in tail_train_idx}) == 2:
            tail_probe.fit(X[tail_train_idx], np.asarray([1 if tail_values[i] else 0 for i in tail_train_idx], dtype=int), sample_weight=w[tail_train_idx], eval_set=(X[tail_eval_idx], np.asarray([1 if tail_values[i] else 0 for i in tail_eval_idx], dtype=int)), use_best_model=True)
            tail_iterations = selected_iterations(tail_probe, max_iter)
        else:
            tail_iterations = max_iter
        tail_native = CatBoostClassifier(loss_function="Logloss", **{**common, "iterations": tail_iterations})
        tail_native.fit(X[tail_indices], tail_labels, sample_weight=w[tail_indices])
        tail_head = export_catboost_model(tail_native, names, "tail")
        tail_head.update(status="available", target="protection_breached")
    else:
        tail_head = {"status": "descriptive_only", "kind": "none", "reason": "insufficient_tail_scope", "tail_delivery_days": len(tail_days), "min_tail_delivery_days_for_precise_probability": min_tail}
    model = {
        "schema": "astra_joint_v11_catboost@1.0.0", "status": "available", "model_id": spec.candidate_id,
        "model_family": "catboost", "feature_group": spec.group, "feature_names": list(names),
        "required_feature_names": required_feature_names(names),
        "hyperparameters": {"depth": int(spec.depth or 3), "learning_rate": float(protocol["catboost_learning_rate"]), "max_iterations": max_iter, "classifier_iterations": classifier_iterations, "regressor_iterations": regressor_iterations},
        "catboost_classifier": export_catboost_model(classifier, names, "classifier"),
        "catboost_regressor": export_catboost_model(regressor, names, "regressor"),
        "catboost_tail": tail_head, "tail_head": tail_head,
        "_runtime_classifier": classifier, "_runtime_regressor": regressor, "_runtime_tail": tail_native,
        "calibrators": {}, "eligibility": qual,
        "tail_summary": {"es95_all_normalized": es95([float(r["_target_loss"]) for r in fit_rows], date_weights(fit_rows)), "tail_delivery_days": len(tail_days)},
        "feature_support": feature_support(fit_rows, names),
        "sample_weighting": "delivery_day_equal_scaled_mean_one",
        "catboost_internal_validation": {"fit_rows": len(train_idx), "eval_rows": len(eval_idx), "classifier_selected_iterations": classifier_iterations, "regressor_selected_iterations": regressor_iterations, "full_refit_rows": len(fit_rows), "full_refit_delivery_days": len(unique_days(fit_rows))},
    }
    if cal_rows:
        cw = date_weights(cal_rows)
        probs, sevs, tails = raw_parts(cal_rows, model)
        cy = [float(r["_target_loss"]) for r in cal_rows]
        model["calibrators"]["occurrence"] = calibrate_probability([1.0 if v > 0 else 0.0 for v in cy], probs, cw)
        model["calibrators"]["positive_severity"] = calibrate_scale([v for v in cy if v > 0], [p for v, p in zip(cy, sevs) if v > 0], [x for v, x in zip(cy, cw) if v > 0])
        tail_pairs = [(r, p, x) for r, p, x in zip(cal_rows, tails, cw) if protection_breached(r) is not None and math.isfinite(p)]
        if tail_pairs:
            model["calibrators"]["tail"] = calibrate_probability([1.0 if protection_breached(r) else 0.0 for r, _, _ in tail_pairs], [p for _, p, _ in tail_pairs], [x for _, _, x in tail_pairs])
    return model


def brier(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float]) -> float | None:
    return weighted_mean([(p - y) ** 2 for y, p in zip(labels, probs)], weights)


def log_loss(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float]) -> float | None:
    losses = []
    for y, p in zip(labels, probs):
        p = min(max(float(p), 1e-12), 1 - 1e-12)
        losses.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return weighted_mean(losses, weights)


def calibration_mae(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float], bins: int = 5) -> float | None:
    buckets: list[list[tuple[float, float, float]]] = [[] for _ in range(bins)]
    for y, p, w in zip(labels, probs, weights):
        buckets[min(bins - 1, max(0, int(p * bins)))].append((y, p, w))
    gaps, gap_weights = [], []
    for bucket in buckets:
        if not bucket:
            continue
        total = sum(w for _, _, w in bucket)
        gaps.append(abs(sum(y * w for y, _, w in bucket) / total - sum(p * w for _, p, w in bucket) / total))
        gap_weights.append(total)
    return weighted_mean(gaps, gap_weights) if gaps else None


def pair_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row.get("observation_id") or row.get("as_of_ms") or row.get("entry_ms"), row.get("entry_ms"), row.get("expiry_ms"), row.get("target_width"), row.get("actual_width"))


def side_accuracy(rows: Sequence[Mapping[str, Any]], predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(dict)
    for r, p in zip(rows, predictions):
        side_key = canonical_side(r.get("side")) or str(r.get("side"))
        grouped[pair_key(r)][side_key] = (r, p)
    decided = correct = ties = same_width_decided = same_width_correct = 0
    for sides in grouped.values():
        if "put_credit" not in sides or "call_credit" not in sides:
            continue
        pr, pp = sides["put_credit"]; cr, cp = sides["call_credit"]
        pe, ce = finite_float(pp.get("expected_loss_normalized")), finite_float(cp.get("expected_loss_normalized"))
        pa, ca = target_loss(pr), target_loss(cr)
        if pe is None or ce is None or pa is None or ca is None:
            continue
        same_width = finite_float(pr.get("actual_width")) is not None and finite_float(cr.get("actual_width")) is not None and abs(float(finite_float(pr.get("actual_width"))) - float(finite_float(cr.get("actual_width")))) <= 1e-9
        if abs(pe - ce) <= 1e-12 or abs(pa - ca) <= 1e-12:
            ties += 1; continue
        decided += 1
        ok = ("put_credit" if pe < ce else "call_credit") == ("put_credit" if pa < ca else "call_credit")
        correct += ok
        if same_width:
            same_width_decided += 1
            same_width_correct += ok
    return {
        "comparable_pairs": sum(1 for v in grouped.values() if "put_credit" in v and "call_credit" in v),
        "decided_pairs": decided,
        "ties": ties,
        "accuracy": correct / decided if decided else None,
        "same_actual_width_decided_pairs": same_width_decided,
        "same_actual_width_accuracy": same_width_correct / same_width_decided if same_width_decided else None,
    }


def daily_error_rows(rows: Sequence[Mapping[str, Any]], predictions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[tuple[float, float, float | None, float | None, float | None]]] = defaultdict(list)
    for row, pred in zip(rows, predictions):
        expected = finite_float(pred.get("expected_loss_normalized"))
        actual = target_loss(row)
        tail_prob = finite_float(pred.get("tail_probability"))
        if expected is None or actual is None:
            continue
        by_day[str(row["_delivery_date"])].append((expected, actual, tail_prob, finite_float(pred.get("expected_payout_btc")), target_payout(row)))
    out = []
    for day in sorted(by_day):
        values = by_day[day]
        squared = [(p - a) ** 2 for p, a, *_ in values]
        absolute = [abs(p - a) for p, a, *_ in values]
        btc_pairs = [(float(pred_btc), float(actual_btc)) for *_, pred_btc, actual_btc in values if pred_btc is not None and actual_btc is not None]
        row = {"delivery_date": day, "row_count": len(values), "expected_loss_mse": sum(squared) / len(squared), "expected_loss_mae": sum(absolute) / len(absolute)}
        if btc_pairs:
            row["expected_payout_btc_mse"] = sum((p - a) ** 2 for p, a in btc_pairs) / len(btc_pairs)
            row["expected_payout_btc_mae"] = sum(abs(p - a) for p, a in btc_pairs) / len(btc_pairs)
        else:
            row["expected_payout_btc_mse"] = None
            row["expected_payout_btc_mae"] = None
        out.append(row)
    return out


def seven_day_blocks(daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day = {str(row["delivery_date"]): row for row in daily_rows}
    if not by_day:
        return []
    start = datetime.strptime(min(by_day), "%Y-%m-%d").date()
    end = datetime.strptime(max(by_day), "%Y-%m-%d").date()
    blocks = []
    cursor = start
    while cursor <= end:
        dates = [(cursor + timedelta(days=offset)).isoformat() for offset in range(7)]
        chunk = [by_day[day] for day in dates if day in by_day]
        if not chunk:
            cursor += timedelta(days=7)
            continue
        mse_values = [float(item["expected_loss_mse"]) for item in chunk]
        blocks.append({
            "start_delivery_date": dates[0],
            "end_delivery_date": dates[-1],
            "day_count": len(chunk),
            "missing_day_count": len(dates) - len(chunk),
            "row_count": int(sum(float(item.get("row_count") or 0) for item in chunk)),
            "expected_loss_mse": sum(mse_values) / len(mse_values),
        })
        cursor += timedelta(days=7)
    return blocks


def evaluate_predictions(rows: Sequence[dict[str, Any]], model: Mapping[str, Any], preds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows or model.get("status") != "available":
        return {"status": "unavailable", "row_count": len(rows)}
    y, w = [float(r["_target_loss"]) for r in rows], date_weights(rows)
    expected = [float(p["expected_loss_normalized"]) for p in preds]
    btc_pairs = [
        (float(pred_btc), float(actual_btc), weight)
        for pred_btc, actual_btc, weight in (
            (finite_float(pred.get("expected_payout_btc")), target_payout(row), wv)
            for row, pred, wv in zip(rows, preds, w)
        )
        if pred_btc is not None and actual_btc is not None
    ]
    labels = [1.0 if v > 0 else 0.0 for v in y]
    probs = [float(p["probability_positive"]) for p in preds]
    tail_pairs = [(1.0 if protection_breached(r) else 0.0, finite_float(p.get("tail_probability")), wv) for r, p, wv in zip(rows, preds, w) if protection_breached(r) is not None and finite_float(p.get("tail_probability")) is not None]
    daily = daily_error_rows(rows, preds)
    blocks = seven_day_blocks(daily)
    return {
        "status": "available", "row_count": len(rows), "delivery_days": len(unique_days(rows)),
        "expected_loss_mse": weighted_mean([(p - a) ** 2 for p, a in zip(expected, y)], w),
        "expected_loss_mae": weighted_mean([abs(p - a) for p, a in zip(expected, y)], w),
        "expected_payout_btc_mse": weighted_mean([(p - a) ** 2 for p, a, _ in btc_pairs], [weight for _, _, weight in btc_pairs]) if btc_pairs else None,
        "expected_payout_btc_mae": weighted_mean([abs(p - a) for p, a, _ in btc_pairs], [weight for _, _, weight in btc_pairs]) if btc_pairs else None,
        "occurrence_brier": brier(labels, probs, w), "occurrence_log_loss": log_loss(labels, probs, w),
        "occurrence_calibration_mae": calibration_mae(labels, probs, w),
        "realized_es95_all_normalized": es95(y, w), "mean_prediction_upper5pct": es95(expected, w),
        "tail_probability_status": (model.get("tail_head") or {}).get("status"),
        "tail_probability_support_rows": len(tail_pairs),
        "tail_probability_brier": brier([x[0] for x in tail_pairs], [float(x[1]) for x in tail_pairs], [x[2] for x in tail_pairs]) if tail_pairs else None,
        "tail_probability_calibration_mae": calibration_mae([x[0] for x in tail_pairs], [float(x[1]) for x in tail_pairs], [x[2] for x in tail_pairs]) if tail_pairs else None,
        "side_choice_accuracy": side_accuracy(rows, preds),
        "daily_expected_loss": daily,
        "seven_day_expected_loss_blocks": blocks,
    }


def evaluate(rows: Sequence[dict[str, Any]], model: Mapping[str, Any]) -> dict[str, Any]:
    if not rows or model.get("status") != "available":
        return {"status": "unavailable", "row_count": len(rows)}
    return evaluate_predictions(rows, model, fast_predictions(rows, model))


def safe_candidate_filename(candidate_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in candidate_id)


ROLLING_PREDICTION_FIELDS = [
    "candidate_id", "fold", "feature_group", "model_family", "model_id",
    "row_id", "observation_id", "as_of_ms", "entry_ms", "expiry_ms", "delivery_date",
    "side", "target_width", "actual_width",
    "expected_loss_normalized", "probability_positive", "conditional_positive_loss",
    "breach_probability", "tail_probability_status",
    "actual_loss_normalized", "actual_payout_btc", "protection_breached",
]


def append_rolling_predictions(path: Path, fold: str, rows: Sequence[Mapping[str, Any]], predictions: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROLLING_PREDICTION_FIELDS)
        if not exists:
            writer.writeheader()
        for row, pred in zip(rows, predictions):
            writer.writerow({
                "candidate_id": model.get("model_id"),
                "fold": fold,
                "feature_group": model.get("feature_group"),
                "model_family": model.get("model_family"),
                "model_id": model.get("model_id"),
                "row_id": row.get("row_id"),
                "observation_id": row.get("observation_id"),
                "as_of_ms": row.get("as_of_ms"),
                "entry_ms": row.get("entry_ms"),
                "expiry_ms": row.get("expiry_ms"),
                "delivery_date": row.get("_delivery_date"),
                "side": row.get("side"),
                "target_width": row.get("target_width"),
                "actual_width": row.get("actual_width"),
                "expected_loss_normalized": pred.get("expected_loss_normalized"),
                "probability_positive": pred.get("probability_positive"),
                "conditional_positive_loss": pred.get("conditional_positive_loss"),
                "breach_probability": pred.get("breach_probability"),
                "tail_probability_status": (model.get("tail_head") or {}).get("status"),
                "actual_loss_normalized": row.get("_target_loss"),
                "actual_payout_btc": row.get("_target_payout_btc"),
                "protection_breached": protection_breached(row),
            })


def specs(groups: Mapping[str, Sequence[str]], protocol: Mapping[str, Any], include_catboost: bool) -> list[CandidateSpec]:
    out = []
    for group in groups:
        for c in protocol["logistic_C"]:
            for a in protocol["gamma_alpha"]:
                out.append(CandidateSpec("gam", group, float(c), float(a)))
        if include_catboost:
            for depth in protocol["catboost_depth"]:
                out.append(CandidateSpec("catboost", group, depth=int(depth)))
    return out


def aggregate(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [x for x in items if x.get("status") == "available"]
    if not ok:
        return {"status": "unavailable", "fold_count": 0, "raw_fold_count": len(items)}
    weights = [float(x.get("delivery_days") or 1) for x in ok]
    result: dict[str, Any] = {"status": "available", "fold_count": len(ok), "raw_fold_count": len(items)}
    for key in ("expected_loss_mse", "expected_loss_mae", "expected_payout_btc_mse", "expected_payout_btc_mae", "occurrence_brier", "occurrence_log_loss", "occurrence_calibration_mae", "realized_es95_all_normalized", "mean_prediction_upper5pct", "tail_probability_brier", "tail_probability_calibration_mae"):
        pairs = [(finite_float(x.get(key)), w) for x, w in zip(ok, weights)]
        pairs = [(float(v), w) for v, w in pairs if v is not None]
        result[key] = weighted_mean([v for v, _ in pairs], [w for _, w in pairs]) if pairs else None
    side_pairs = [(finite_float((x.get("side_choice_accuracy") or {}).get("accuracy")), w) for x, w in zip(ok, weights)]
    side_pairs = [(float(v), w) for v, w in side_pairs if v is not None]
    result["side_choice_accuracy"] = weighted_mean([v for v, _ in side_pairs], [w for _, w in side_pairs]) if side_pairs else None
    daily_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    for item in ok:
        for day in item.get("daily_expected_loss") or []:
            daily_rows.append(dict(day))
        for block in item.get("seven_day_expected_loss_blocks") or []:
            block_rows.append(dict(block))
    result["validation_day_count"] = len(daily_rows)
    result["validation_seven_day_block_count"] = len(block_rows)
    mse_values = [float(x["expected_loss_mse"]) for x in block_rows if finite_float(x.get("expected_loss_mse")) is not None]
    if len(mse_values) > 1:
        mean = sum(mse_values) / len(mse_values)
        result["expected_loss_mse_se"] = math.sqrt(sum((x - mean) ** 2 for x in mse_values) / (len(mse_values) - 1) / len(mse_values))
    else:
        result["expected_loss_mse_se"] = None
    result["daily_expected_loss"] = daily_rows
    result["seven_day_expected_loss_blocks"] = block_rows
    if len(ok) != len(items):
        result["status"] = "unavailable"
        result["reason"] = "partial_validation_scope"
        result["available_fold_count"] = len(ok)
        result["unavailable_fold_count"] = len(items) - len(ok)
    return result


def train_candidate(
    rows: Sequence[dict[str, Any]],
    spec: CandidateSpec,
    groups: Mapping[str, Sequence[str]],
    protocol: Mapping[str, Any],
    rolling_predictions_dir: str | Path | None = None,
) -> dict[str, Any]:
    fold_metrics = []
    prediction_path: Path | None = None
    if rolling_predictions_dir is not None:
        prediction_path = Path(rolling_predictions_dir) / f"{safe_candidate_filename(spec.candidate_id)}.csv"
        prediction_path.parent.mkdir(parents=True, exist_ok=True)
        prediction_path.write_text("", encoding="utf-8")
    for fold in rolling_windows(protocol):
        fit, fit_purge = between_with_purge(rows, fold["train_start"], fold["cal_start"])
        cal, cal_purge = between_with_purge(rows, fold["cal_start"], fold["eval_start"])
        ev, ev_purge = between_with_purge(rows, fold["eval_start"], fold["eval_end"])
        filtered, overlap = purge_overlapping_delivery_dates({"fit": fit, "calibration": cal, "evaluation": ev})
        fit, cal, ev = filtered["fit"], filtered["calibration"], filtered["evaluation"]
        purged_rows = {"fit": fit_purge, "calibration": cal_purge, "evaluation": ev_purge, **overlap}
        if not ev:
            fold_metrics.append({"status": "unavailable", "reason": "empty_eval", "fold": fold["name"], "purged_rows": purged_rows}); continue
        if spec.family == "gam":
            model = fit_gam(fit, cal, groups[spec.group], spec, protocol)
        elif spec.family == "catboost":
            try:
                model = fit_catboost(fit, cal, groups[spec.group], spec, protocol)
            except ImportError as exc:
                model = {"status": "unavailable", "reason": f"catboost_import_error: {exc}"}
            except Exception as exc:
                model = {"status": "unavailable", "reason": f"catboost_train_error: {type(exc).__name__}: {exc}"}
        else:
            model = {"status": "unavailable", "reason": f"unknown_family: {spec.family}"}
        if model.get("status") == "available":
            preds = fast_predictions(ev, model)
            metric = evaluate_predictions(ev, model, preds)
            if prediction_path is not None:
                append_rolling_predictions(prediction_path, fold["name"], ev, preds, model)
        else:
            metric = {"status": "unavailable", "reason": model.get("reason"), "eligibility": model.get("eligibility")}
        metric["fold"] = fold["name"]
        metric["purged_rows"] = purged_rows
        fold_metrics.append(metric)
    metrics = aggregate(fold_metrics)
    hp = {"logistic_C": spec.logistic_c, "gamma_alpha": spec.gamma_alpha} if spec.family == "gam" else {"depth": spec.depth, "learning_rate": protocol["catboost_learning_rate"], "max_iterations": protocol["catboost_max_iterations"]}
    return {
        "candidate_id": spec.candidate_id,
        "model_family": spec.family,
        "feature_group": spec.group,
        "hyperparameters": hp,
        "status": metrics["status"],
        "exportable_stdlib": metrics["status"] == "available",
        "rolling_metrics": metrics,
        "fold_metrics": fold_metrics,
        "rolling_prediction_path": str(prediction_path) if prediction_path is not None else None,
    }


def select_candidate(summaries: Sequence[Mapping[str, Any]], groups: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    ok = [x for x in summaries if x.get("status") == "available" and x.get("exportable_stdlib") and finite_float((x.get("rolling_metrics") or {}).get("expected_loss_mse")) is not None]
    if not ok:
        return {"status": "unavailable", "reason": "no_available_exportable_candidate"}
    best = min(ok, key=lambda x: float(x["rolling_metrics"]["expected_loss_mse"]))
    paired = paired_differences_vs(best, ok)
    best_mse = float(best["rolling_metrics"]["expected_loss_mse"])
    paired_by_candidate = {str(item.get("candidate_id")): item for item in paired}
    fallback_se = finite_float(best["rolling_metrics"].get("expected_loss_mse_se"))
    within = []
    for candidate in ok:
        if candidate["candidate_id"] == best["candidate_id"]:
            within.append(candidate)
            continue
        comparison = paired_by_candidate.get(str(candidate.get("candidate_id"))) or {}
        diff = finite_float(comparison.get("mean_seven_day_mse_difference_candidate_minus_best"))
        se = finite_float(comparison.get("se_seven_day_mse_difference_candidate_minus_best"))
        if diff is not None and se is not None and diff <= se:
            within.append(candidate)
        elif diff is None and fallback_se is not None and float(candidate["rolling_metrics"]["expected_loss_mse"]) <= best_mse + fallback_se:
            within.append(candidate)
    def simple(x: Mapping[str, Any]) -> tuple[int, int, float, float, float]:
        hp = x.get("hyperparameters") or {}
        if x.get("model_family") == "gam":
            return (0, len(groups.get(str(x.get("feature_group")), ())), 0.0, float(hp.get("logistic_C") or 999), -float(hp.get("gamma_alpha") or 0))
        return (1, len(groups.get(str(x.get("feature_group")), ())), float(hp.get("depth") or 999), 999.0, 0.0)
    selected = min(within, key=simple)
    selected_comparison = paired_by_candidate.get(str(selected.get("candidate_id"))) or {}
    return {
        "status": "available",
        "selected_candidate_id": selected["candidate_id"],
        "selected_feature_group": selected["feature_group"],
        "selected_model_family": selected["model_family"],
        "best_by_mse_candidate_id": best["candidate_id"],
        "best_expected_loss_mse": best_mse,
        "one_standard_error": selected_comparison.get("se_seven_day_mse_difference_candidate_minus_best"),
        "selection_rule": "primary expected_loss_mse; candidate-specific paired calendar-seven-day block SE; within one SE favor exportable simpler stronger-regularized GAM",
        "paired_validation_differences_vs_best": paired,
    }


def paired_differences_vs(best: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    best_days = {
        str(day["delivery_date"]): float(day["expected_loss_mse"])
        for day in ((best.get("rolling_metrics") or {}).get("daily_expected_loss") or [])
        if finite_float(day.get("expected_loss_mse")) is not None
    }
    out = []
    for candidate in candidates:
        cand_days = {
            str(day["delivery_date"]): float(day["expected_loss_mse"])
            for day in ((candidate.get("rolling_metrics") or {}).get("daily_expected_loss") or [])
            if finite_float(day.get("expected_loss_mse")) is not None
        }
        common = sorted(set(best_days) & set(cand_days))
        if common:
            start = datetime.strptime(common[0], "%Y-%m-%d").date()
            end = datetime.strptime(common[-1], "%Y-%m-%d").date()
        else:
            start = end = None
        block_diffs = []
        cursor = start
        while cursor is not None and end is not None and cursor <= end:
            dates = [(cursor + timedelta(days=offset)).isoformat() for offset in range(7)]
            vals = [cand_days[day] - best_days[day] for day in dates if day in cand_days and day in best_days]
            if vals:
                block_diffs.append({
                    "start_delivery_date": dates[0],
                    "end_delivery_date": dates[-1],
                    "day_count": len(vals),
                    "missing_day_count": len(dates) - len(vals),
                    "mean_mse_difference_candidate_minus_best": sum(vals) / len(vals),
                })
            cursor += timedelta(days=7)
        block_vals = [float(item["mean_mse_difference_candidate_minus_best"]) for item in block_diffs]
        mean_block = sum(block_vals) / len(block_vals) if block_vals else None
        se_block = None
        if len(block_vals) > 1:
            se_block = math.sqrt(sum((x - float(mean_block)) ** 2 for x in block_vals) / (len(block_vals) - 1) / len(block_vals))
        out.append({
            "candidate_id": candidate.get("candidate_id"),
            "best_candidate_id": best.get("candidate_id"),
            "common_validation_days": len(common),
            "seven_day_block_count": len(block_diffs),
            "mean_seven_day_mse_difference_candidate_minus_best": mean_block,
            "se_seven_day_mse_difference_candidate_minus_best": se_block,
            "seven_day_blocks": block_diffs,
        })
    return out


def fit_final_model(
    fit: Sequence[dict[str, Any]],
    cal: Sequence[dict[str, Any]],
    group: str,
    candidate: Mapping[str, Any],
    groups: Mapping[str, Sequence[str]],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    hp = candidate["hyperparameters"]
    family = str(candidate.get("model_family"))
    if family == "catboost":
        return fit_catboost(fit, cal, groups[group], CandidateSpec("catboost", group, depth=int(hp["depth"])), protocol)
    return fit_gam(fit, cal, groups[group], CandidateSpec("gam", group, float(hp["logistic_C"]), float(hp["gamma_alpha"])), protocol)


def train_run(
    rows: Sequence[dict[str, Any]],
    groups: Mapping[str, Sequence[str]],
    protocol: Mapping[str, Any],
    final_end: str,
    include_catboost: bool = True,
    rolling_predictions_dir: str | Path | None = None,
) -> dict[str, Any]:
    summaries = []
    candidates = specs(groups, protocol, include_catboost)
    for index, spec in enumerate(candidates, start=1):
        print(
            json.dumps({"event": "candidate_start", "index": index, "total": len(candidates), "candidate_id": spec.candidate_id}),
            file=sys.stderr,
            flush=True,
        )
        summary = train_candidate(rows, spec, groups, protocol, rolling_predictions_dir=rolling_predictions_dir)
        summaries.append(summary)
        print(
            json.dumps({
                "event": "candidate_complete",
                "index": index,
                "total": len(candidates),
                "candidate_id": spec.candidate_id,
                "status": summary.get("status"),
                "expected_loss_mse": (summary.get("rolling_metrics") or {}).get("expected_loss_mse"),
            }),
            file=sys.stderr,
            flush=True,
        )
    selection = select_candidate(summaries, groups)
    if selection["status"] != "available":
        raise ValueError(f"selection failed: {selection}")
    fit, cal, final_meta = final_split(rows, final_end, protocol)
    models, final_metrics, group_selections = {}, {}, {}
    final_predictions: list[dict[str, Any]] = []
    summaries_by_id = {str(item.get("candidate_id")): item for item in summaries}
    for group in groups:
        group_summaries = [x for x in summaries if x.get("feature_group") == group and x.get("status") == "available" and x.get("exportable_stdlib")]
        if not group_summaries:
            continue
        if group == selection["selected_feature_group"]:
            group_selection = selection
            chosen = summaries_by_id.get(str(selection["selected_candidate_id"]))
            if chosen is None or chosen.get("feature_group") != group:
                raise ValueError(f"selected candidate not found for group {group}: {selection['selected_candidate_id']}")
        else:
            group_selection = select_candidate(group_summaries, {group: groups[group]})
            if group_selection["status"] != "available":
                continue
            chosen = summaries_by_id.get(str(group_selection["selected_candidate_id"]))
            if chosen is None:
                raise ValueError(f"group selected candidate not found for {group}: {group_selection['selected_candidate_id']}")
        group_selections[group] = group_selection
        print(
            json.dumps({"event": "final_refit_start", "feature_group": group, "candidate_id": chosen.get("candidate_id")}),
            file=sys.stderr,
            flush=True,
        )
        model = fit_final_model(fit, cal, group, chosen, groups, protocol)
        if model.get("status") == "available":
            models[group] = model
            final_metrics[group] = evaluate(cal, model) if cal else {"status": "unavailable", "reason": "empty_calibration"}
            if cal:
                for row, pred in zip(cal, fast_predictions(cal, model)):
                    final_predictions.append({
                        "feature_group": group,
                        "model_family": model.get("model_family"),
                        "model_id": model.get("model_id"),
                        "row_id": row.get("row_id"),
                        "observation_id": row.get("observation_id"),
                        "as_of_ms": row.get("as_of_ms"),
                        "entry_ms": row.get("entry_ms"),
                        "expiry_ms": row.get("expiry_ms"),
                        "delivery_date": row.get("_delivery_date"),
                        "side": row.get("side"),
                        "target_width": row.get("target_width"),
                        "actual_width": row.get("actual_width"),
                        "expected_loss_normalized": pred.get("expected_loss_normalized"),
                        "probability_positive": pred.get("probability_positive"),
                        "conditional_positive_loss": pred.get("conditional_positive_loss"),
                        "breach_probability": pred.get("breach_probability"),
                        "tail_probability_status": (model.get("tail_head") or {}).get("status"),
                        "actual_loss_normalized": row.get("_target_loss"),
                        "actual_payout_btc": row.get("_target_payout_btc"),
                        "protection_breached": protection_breached(row),
                    })
    if selection["selected_feature_group"] not in models:
        raise ValueError("selected group missing final model")
    return {
        "candidate_summaries": summaries,
        "selection": selection,
        "group_selections": group_selections,
        "selected_feature_group": selection["selected_feature_group"],
        "models": models,
        "final_window": final_meta,
        "final_calibration_metrics": final_metrics,
        "final_validation_predictions": final_predictions,
    }


def write_outputs(output_dir: str | Path, run: Mapping[str, Any], groups: Mapping[str, Sequence[str]], protocol: Mapping[str, Any], contract_status: Mapping[str, Any], inputs: Sequence[str | Path], gaps: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    models = strip_runtime(run["models"])
    cutoff = date_utc(str(run["final_window"]["window_end"]))
    cutoff_dt = cutoff.replace(hour=23, minute=59, second=59, microsecond=999000)
    cutoff_ms = ms(cutoff_dt)
    for model in models.values():
        model["training_cutoff_ms"] = cutoff_ms
        model["training_cutoff"] = cutoff_dt.isoformat()
        model["model_hash"] = digest_value({k: v for k, v in model.items() if k != "model_hash"})
    artifact = {
        "schema": ARTIFACT_SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol, "contract_status": contract_status, "feature_groups": {k: list(v) for k, v in groups.items()},
        "selected_feature_group": run["selected_feature_group"], "selection": run["selection"], "group_selections": run.get("group_selections"), "models": models,
        "final_window": run["final_window"], "final_calibration_metrics": run["final_calibration_metrics"],
        "source_files": [{"path": str(p), "sha256": digest_file(p)} for p in inputs], "input_gap_count": len(gaps),
        "training_cutoff_ms": cutoff_ms,
        "training_cutoff": cutoff_dt.isoformat(),
        "read_stats": run.get("read_stats"),
        "dataset_manifest_status": run.get("dataset_manifest_status"),
    }
    artifact["artifact_hash_without_self"] = digest_value(artifact)
    selected_model = models.get(run["selected_feature_group"])
    portable = {
        **{k: v for k, v in artifact.items() if k != "models"},
        "models": {run["selected_feature_group"]: selected_model} if selected_model else {},
        "portable_scope": "selected_feature_group_only",
    }
    portable["artifact_hash_without_self"] = digest_value(portable)
    report = {"schema": "astra_joint_v11_training_report@1.0.0", "created_at": artifact["created_at"], "selection": run["selection"], "group_selections": run.get("group_selections"), "candidate_summaries": run["candidate_summaries"], "final_window": run["final_window"], "final_calibration_metrics": run["final_calibration_metrics"], "read_stats": run.get("read_stats"), "dataset_manifest_status": run.get("dataset_manifest_status"), "input_gap_count": len(gaps), "input_gaps_first_1000": list(gaps[:1000])}
    artifact_path, report_path = out / "astra_joint_v11_model_artifact.json", out / "astra_joint_v11_training_report.json"
    portable_path = out / "astra_joint_v11_portable_selected_model.json"
    predictions_path = out / "astra_joint_v11_final_validation_predictions.csv"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    portable_path.write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prediction_rows = list(run.get("final_validation_predictions") or [])
    if prediction_rows:
        fields = list(prediction_rows[0].keys())
        with predictions_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(prediction_rows)
    else:
        predictions_path.write_text("", encoding="utf-8")
    return {"artifact": str(artifact_path), "portable_selected_model": str(portable_path), "report": str(report_path), "final_validation_predictions": str(predictions_path)}


def train_from_inputs(inputs: Sequence[str | Path], output_dir: str | Path, final_end: str = DEFAULT_FINAL_END, include_catboost: bool = True, dataset_root: str | Path | None = None) -> dict[str, Any]:
    groups, protocol, contract_status = load_contract()
    manifest, manifest_status = load_dataset_manifest(dataset_root_from_inputs(inputs, dataset_root))
    manifest_groups, protocol, manifest_feature_status = apply_manifest_features(groups, protocol, manifest)
    groups = manifest_groups
    contract_status = {**contract_status, "dataset_manifest": manifest_status, "manifest_features": manifest_feature_status}
    raw_rows, read_stats = read_training_rows(inputs, groups, protocol)
    rows, gaps = enrich_rows(raw_rows, groups)
    if not rows:
        raise ValueError(f"no eligible rows; gaps={gaps[:5]}")
    rolling_predictions_dir = Path(output_dir) / "rolling_candidate_predictions"
    run = train_run(rows, groups, protocol, final_end, include_catboost, rolling_predictions_dir=rolling_predictions_dir)
    run["read_stats"] = read_stats
    run["dataset_manifest_status"] = contract_status.get("dataset_manifest")
    paths = write_outputs(output_dir, run, groups, protocol, contract_status, inputs, gaps)
    return {"schema": "astra_joint_v11_train_result@1.0.0", "eligible_rows": len(rows), "gap_count": len(gaps), "read_stats": read_stats, "selection": run["selection"], "final_window": run["final_window"], "paths": paths}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train Astra joint v1.1 NR-compatible model")
    parser.add_argument("command", nargs="?", default="train")
    parser.add_argument("--input", action="append")
    parser.add_argument("--root")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--final-end", default=DEFAULT_FINAL_END)
    parser.add_argument("--skip-catboost", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "train":
        raise SystemExit("only train command is supported")
    inputs = [Path(x) for x in (args.input or [])] or (discover_csvs(args.root) if args.root else [])
    if not inputs:
        raise SystemExit("no input CSVs provided or discovered")
    print(json.dumps(train_from_inputs(inputs, args.output_dir, args.final_end, not args.skip_catboost, args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "6")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "6")
    os.environ.setdefault("MKL_NUM_THREADS", "6")
    raise SystemExit(main())
