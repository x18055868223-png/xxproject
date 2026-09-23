"""Research-only native NR reference spread and payout comparison.

This script converts the frozen native NeutralRepair card interface into a
side-by-side reference-leg and payout ledger. It is intentionally separated
from the joint-event training dataset: no model is trained, no 2023 outcome
files are read, and native NR observations are not re-labeled as price
rebalance events.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from astra_joint_contract import PROTOCOL
from astra_joint_dataset import SpotOpenCache, canonical_hash, expiry_ms, payout, select_legs
from astra_joint_inference import predict_row
from astra_joint_sources import digest

SCHEMA = "astra_native_nr_reference_comparison@1.0.0"
PREDICTION_SCHEMA = "astra_native_nr_prediction_request@1.0.0"
MODEL_PREDICTION_SCHEMA = "astra_native_nr_model_prediction@1.0.0"
DEFAULT_ROOT = Path(".artifacts/astra-joint-v1-20260914")
SIDES = ("put", "call")
PRIMARY_WIDTH = 2000
WIDTHS = tuple(PROTOCOL.get("widths", (2000, 1500, 2500)))
MIN_DTE_HOURS = 8.0
MAX_DTE_HOURS = 24.0
MINUTE_MS = 60_000

CARD_LEDGER_FIELDS = [
    "schema",
    "native_sequence",
    "identity_key",
    "identity_duplicate_role",
    "identity_duplicate_group_size",
    "identity_duplicate_relation",
    "sample_weight",
    "source_record_hash",
    "card_id",
    "confirmed_time_ms",
    "confirmed_time_utc",
    "confirmed_date_bjt",
    "strategy_version",
    "record_kind",
    "event_type",
    "episode_id",
    "decision_lean",
    "side_hint",
    "support_label",
    "trade_allowed",
    "entry_ms",
    "entry_utc",
    "expiry_ms",
    "expiry_utc",
    "dte_hours",
    "ordinary_8_24h",
    "entry_price_status",
    "entry_price",
    "entry_price_source",
    "settlement_status",
    "settlement_price",
    "native_nr_interface_eligible",
    "synthetic_excluded",
    "option_structure_fields_present",
    "field_level_options_factor",
    "strict_contract_usable",
    "anchor_available",
    "near_term_context_available",
    "um_rebuilt_status",
    "price_event_overlap_status",
    "price_event_overlap_ids",
    "primary_failure_reason",
]

COMPARISON_FIELDS = [
    "schema",
    "native_sequence",
    "identity_key",
    "identity_duplicate_role",
    "identity_duplicate_group_size",
    "identity_duplicate_relation",
    "sample_weight",
    "row_id",
    "source_record_hash",
    "card_id",
    "confirmed_time_ms",
    "entry_ms",
    "expiry_ms",
    "delivery_date",
    "dte_hours",
    "side",
    "target_width",
    "comparison_bucket",
    "decision_lean",
    "side_hint",
    "entry_price",
    "price_source",
    "result_status",
    "failure_reason",
    "short_strike",
    "long_strike",
    "actual_width",
    "short_name",
    "long_name",
    "short_creation_ms",
    "long_creation_ms",
    "option_source_sha256",
    "settlement_price",
    "payout_btc",
    "loss_normalized",
    "payout_intrusion_at_breakeven_unknown_credit",
    "um_rebuilt_status",
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
    "pressure_response_15",
    "pressure_response_30",
    "range_position_15",
    "range_position_30",
    "range_expansion_15",
    "efficiency_15",
    "efficiency_30",
    "vwap_deviation_15",
    "vwap_deviation_30",
    "vwap_migration_15",
    "adverse_move_since_shock",
    "favorable_move_since_shock",
    "option_structure_fields_present",
    "field_level_options_factor",
    "strict_contract_usable",
    "anchor_available",
    "gamma_regime",
    "net_gamma_notional_usd",
    "put_wall",
    "call_wall",
    "pin_strike",
    "price_event_overlap_status",
    "price_event_overlap_ids",
    "source_observation_hash",
]

PREDICTION_FIELDS = [
    "schema",
    "native_sequence",
    "identity_key",
    "identity_duplicate_role",
    "sample_weight",
    "request_id",
    "row_id",
    "source_record_hash",
    "card_id",
    "entry_ms",
    "expiry_ms",
    "side",
    "target_width",
    "actual_width",
    "entry_price",
    "prediction_status",
    "model_required",
    "feature_scope",
    "source_observation_hash",
]


def utc_iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def to_bool_text(value: Any) -> str:
    if value is None:
        return "unknown"
    return "true" if bool(value) else "false"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for lineno, line in enumerate(stream, 1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid jsonl at {path}:{lineno}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"jsonl row must be object at {path}:{lineno}")
            rows.append(obj)
    return rows


def load_contracts(root: Path) -> tuple[dict[int, list[dict[str, Any]]], str]:
    path = root / "raw" / "deribit" / "instruments.json"
    if not path.exists():
        raise FileNotFoundError(path)
    source_hash = digest(path)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for contract in json.loads(path.read_text("utf-8")):
        if contract.get("kind") not in (None, "option"):
            continue
        if contract.get("settlement_currency") not in (None, "BTC"):
            continue
        expiry = contract.get("expiration_timestamp")
        if expiry is None:
            continue
        option_type = str(contract.get("option_type", "")).lower()
        if option_type not in SIDES:
            continue
        strike = to_float(contract.get("strike"))
        creation = contract.get("creation_timestamp")
        if strike is None or creation is None:
            continue
        normalized = dict(contract)
        normalized["option_type"] = option_type
        normalized["strike"] = float(strike)
        normalized["creation_timestamp"] = int(creation)
        normalized["expiration_timestamp"] = int(expiry)
        grouped[int(expiry)].append(normalized)
    return grouped, source_hash


def load_delivery_prices(root: Path) -> dict[str, float]:
    path = root / "raw" / "deribit" / "delivery_prices.json"
    if not path.exists():
        raise FileNotFoundError(path)
    out: dict[str, float] = {}
    for row in json.loads(path.read_text("utf-8")):
        date = row.get("date")
        price = to_float(row.get("delivery_price"))
        if date and price is not None:
            out[str(date)] = price
    return out


def load_price_event_overlaps(root: Path, native_entry_ms: set[int]) -> dict[int, list[dict[str, Any]]]:
    """Optionally link NR entries to same-minute price rebalance observations."""
    events_path = root / "events" / "observations.jsonl"
    if not events_path.exists() or not native_entry_ms:
        return {}
    wanted_minutes = {ms // MINUTE_MS for ms in native_entry_ms}
    overlaps: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with events_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            entry = row.get("entry_ms")
            as_of = row.get("as_of_ms")
            stamp = int(entry if entry is not None else as_of if as_of is not None else -1)
            if stamp < 0:
                continue
            minute = stamp // MINUTE_MS
            if minute not in wanted_minutes:
                continue
            overlaps[minute].append(
                {
                    "observation_id": row.get("observation_id"),
                    "episode_id": row.get("episode_id"),
                    "observation_kind": row.get("observation_kind"),
                    "entry_ms": entry,
                    "as_of_ms": as_of,
                }
            )
    return overlaps


def native_rows(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in observations:
        identity = row.get("identity") or {}
        role = row.get("research_role") or {}
        if bool(identity.get("is_synthetic")):
            continue
        if identity.get("record_kind") == "native_nr_event" or bool(role.get("eligible_native_nr_interface")):
            out.append(row)
    return out


def identity_key_for(obs: dict[str, Any]) -> str:
    identity = obs.get("identity") or {}
    source = obs.get("source") or {}
    card_id = identity.get("card_id")
    record_hash = source.get("source_record_hash")
    if card_id and record_hash:
        return f"{card_id}::{record_hash}"
    return record_key_for(obs)


def payload_without_source_fact_line(obs: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(obs)
    source = cloned.get("source")
    if isinstance(source, dict):
        source.pop("fact_line", None)
    return cloned


def build_identity_metadata(native: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for sequence, obs in enumerate(native, 1):
        groups[identity_key_for(obs)].append((sequence, obs))

    metadata: dict[int, dict[str, Any]] = {}
    duplicate_groups: list[dict[str, Any]] = []
    for key, rows in groups.items():
        payload_hashes = [canonical_hash(obs) for _, obs in rows]
        canonical_payload_hashes = [canonical_hash(payload_without_source_fact_line(obs)) for _, obs in rows]
        relation = "unique_identity"
        if len(rows) > 1:
            if len(set(payload_hashes)) == 1:
                relation = "exact_duplicate_payload"
            elif len(set(canonical_payload_hashes)) == 1:
                relation = "same_card_and_record_hash_payload_differs_only_source_fact_line"
            else:
                relation = "same_card_and_record_hash_different_payload"
            duplicate_groups.append(
                {
                    "identity_key": key,
                    "count": len(rows),
                    "native_sequences": [sequence for sequence, _ in rows],
                    "payload_hashes": payload_hashes,
                    "canonical_without_source_fact_line_hashes": canonical_payload_hashes,
                    "relation": relation,
                }
            )
        first_sequence = rows[0][0]
        for sequence, _obs in rows:
            metadata[sequence] = {
                "identity_key": key,
                "identity_duplicate_role": "primary_identity" if sequence == first_sequence else "duplicate_identity",
                "identity_duplicate_group_size": len(rows),
                "identity_duplicate_relation": relation,
                "sample_weight": 1 if sequence == first_sequence else 0,
            }
    return metadata, duplicate_groups


def comparison_bucket(side_hint: str | None, side: str) -> str:
    hint = (side_hint or "").lower()
    if hint == "put_credit_spread":
        return "original_direction_side" if side == "put" else "opposite_side"
    if hint == "call_credit_spread":
        return "original_direction_side" if side == "call" else "opposite_side"
    return "neutral_put" if side == "put" else "neutral_call"


def observation_hash(row: dict[str, Any]) -> str:
    return canonical_hash(row)


def row_hash_parts(obs: dict[str, Any], native_sequence: int, side: str, target_width: int) -> list[Any]:
    src = (obs.get("source") or {}).get("source_record_hash")
    card_id = (obs.get("identity") or {}).get("card_id")
    return ["native_nr_reference_payout_v1", native_sequence, src, card_id, side, target_width]


def record_key_for(obs: dict[str, Any]) -> str:
    source = obs.get("source") or {}
    identity = obs.get("identity") or {}
    return str(
        source.get("source_record_hash")
        or canonical_hash(
            [
                identity.get("card_id"),
                identity.get("confirmed_time_ms"),
                source.get("fact_file"),
                source.get("fact_line"),
            ]
        )
    )


def delivery_date_for(expiry: int) -> str:
    return datetime.fromtimestamp(expiry / 1000, timezone.utc).strftime("%Y-%m-%d")


def compact_overlap_ids(overlaps: list[dict[str, Any]]) -> str:
    ids = [str(x.get("observation_id")) for x in overlaps if x.get("observation_id")]
    return ";".join(ids[:8])


def open_price_for(spot: SpotOpenCache, entry_ms: int) -> tuple[str, float | None, str]:
    price = spot.get(entry_ms)
    if price is None:
        return "missing", None, "Binance BTCUSDT spot next-minute open"
    return "available", float(price), "Binance BTCUSDT spot next-minute open"


def card_base_fields(
    obs: dict[str, Any],
    native_sequence: int,
    duplicate_meta: dict[str, Any],
    entry_ms: int | None,
    expiry: int | None,
    dte_hours: float | None,
) -> dict[str, Any]:
    identity = obs.get("identity") or {}
    timing = obs.get("reference_timing") or {}
    markers = obs.get("original_signal_markers") or {}
    availability = obs.get("availability_markers") or {}
    options = availability.get("options") or {}
    anchor = availability.get("anchor") or {}
    source = obs.get("source") or {}
    return {
        "schema": SCHEMA,
        "native_sequence": native_sequence,
        "identity_key": duplicate_meta.get("identity_key"),
        "identity_duplicate_role": duplicate_meta.get("identity_duplicate_role"),
        "identity_duplicate_group_size": duplicate_meta.get("identity_duplicate_group_size"),
        "identity_duplicate_relation": duplicate_meta.get("identity_duplicate_relation"),
        "sample_weight": duplicate_meta.get("sample_weight"),
        "source_record_hash": source.get("source_record_hash"),
        "card_id": identity.get("card_id"),
        "confirmed_time_ms": identity.get("confirmed_time_ms"),
        "confirmed_time_utc": identity.get("confirmed_time_utc"),
        "confirmed_date_bjt": identity.get("confirmed_date_bjt"),
        "strategy_version": identity.get("strategy_version"),
        "record_kind": identity.get("record_kind"),
        "event_type": identity.get("event_type"),
        "episode_id": identity.get("episode_id"),
        "decision_lean": markers.get("decision_lean"),
        "side_hint": markers.get("side_hint"),
        "support_label": markers.get("support_label"),
        "trade_allowed": to_bool_text(markers.get("trade_allowed")),
        "entry_ms": entry_ms,
        "entry_utc": utc_iso(entry_ms),
        "expiry_ms": expiry,
        "expiry_utc": utc_iso(expiry),
        "dte_hours": dte_hours,
        "ordinary_8_24h": to_bool_text(timing.get("ordinary_round_8_24h")),
        "native_nr_interface_eligible": to_bool_text((obs.get("research_role") or {}).get("eligible_native_nr_interface")),
        "synthetic_excluded": "false",
        "option_structure_fields_present": to_bool_text(options.get("structure_fields_present")),
        "field_level_options_factor": to_bool_text(options.get("field_level_options_factor")),
        "strict_contract_usable": to_bool_text(options.get("strict_contract_usable")),
        "anchor_available": to_bool_text(anchor.get("available")),
        "near_term_context_available": to_bool_text(availability.get("near_term_market_context")),
        "um_rebuilt_status": (obs.get("um_rebuilt_features") or {}).get("status") or "missing",
    }


def option_marker_fields(obs: dict[str, Any]) -> dict[str, Any]:
    options = ((obs.get("availability_markers") or {}).get("options") or {})
    return {
        "option_structure_fields_present": to_bool_text(options.get("structure_fields_present")),
        "field_level_options_factor": to_bool_text(options.get("field_level_options_factor")),
        "strict_contract_usable": to_bool_text(options.get("strict_contract_usable")),
        "gamma_regime": options.get("gamma_regime"),
        "net_gamma_notional_usd": options.get("net_gamma_notional_usd"),
        "put_wall": options.get("put_wall"),
        "call_wall": options.get("call_wall"),
        "pin_strike": options.get("pin_strike"),
    }


def rebuilt_feature_fields(obs: dict[str, Any]) -> dict[str, Any]:
    feat = obs.get("um_rebuilt_features") or {}
    keys = (
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
        "pressure_response_15",
        "pressure_response_30",
        "range_position_15",
        "range_position_30",
        "range_expansion_15",
        "efficiency_15",
        "efficiency_30",
        "vwap_deviation_15",
        "vwap_deviation_30",
        "vwap_migration_15",
        "adverse_move_since_shock",
        "favorable_move_since_shock",
    )
    out = {key: feat.get(key) for key in keys}
    out["um_rebuilt_status"] = feat.get("status") or "missing"
    return out


def hour_fraction(ms: int) -> float:
    value = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return (value.hour * 60 + value.minute + value.second / 60.0) / (24.0 * 60.0)


def hour_sin(ms: int) -> float:
    return math.sin(2.0 * math.pi * hour_fraction(ms))


def hour_cos(ms: int) -> float:
    return math.cos(2.0 * math.pi * hour_fraction(ms))


def load_portable_models(root: Path) -> dict[str, dict[str, Any]]:
    portable = root / "portable"
    specs = {
        "champion_statistical": "model.json",
        "joint": "joint.json",
        "mechanism": "mechanism.json",
    }
    models: dict[str, dict[str, Any]] = {}
    for label, name in specs.items():
        path = portable / name
        if not path.exists():
            continue
        artifact = json.loads(path.read_text("utf-8"))
        models[label] = {
            "label": label,
            "path": str(path),
            "file": name,
            "sha256": digest(path),
            "artifact": artifact,
            "artifact_hash": artifact.get("artifact_hash"),
            "feature_group": artifact.get("feature_group"),
            "model_version": artifact.get("model_version"),
            "model_kind": artifact.get("model_kind"),
            "training_cutoff": artifact.get("training_cutoff"),
        }
    return models


def required_features(artifact: dict[str, Any]) -> list[str]:
    specs = ((artifact.get("preprocess") or {}).get("numeric_features") or [])
    return [str(spec.get("name")) for spec in specs if isinstance(spec, dict) and spec.get("name")]


def missing_features_for(artifact: dict[str, Any], model_row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for name in required_features(artifact):
        value = model_row.get(name)
        if name == "side_sign":
            value = -1.0 if str(model_row.get("side")).lower() == "put" else 1.0 if str(model_row.get("side")).lower() == "call" else value
        if to_float(value) is None:
            missing.append(name)
    return missing


def outside_support_for(artifact: dict[str, Any], model_row: dict[str, Any]) -> list[str]:
    support = ((artifact.get("scope") or {}).get("training_feature_support") or {})
    outside: list[str] = []
    for name, limits in support.items():
        if not isinstance(limits, dict):
            continue
        value = -1.0 if name == "side_sign" and str(model_row.get("side")).lower() == "put" else 1.0 if name == "side_sign" and str(model_row.get("side")).lower() == "call" else to_float(model_row.get(name))
        if value is None:
            continue
        low = to_float(limits.get("min"))
        high = to_float(limits.get("max"))
        if low is not None and value < low:
            outside.append(name)
        elif high is not None and value > high:
            outside.append(name)
    return outside


def model_input_row(obs: dict[str, Any], base: dict[str, Any], side: str) -> dict[str, Any]:
    markers = obs.get("original_signal_markers") or {}
    row = dict(base)
    entry_ms = int(base["entry_ms"])
    entry_price = float(base["entry_price"])
    actual_width = float(base["actual_width"])
    short_strike = float(base["short_strike"])
    long_strike = float(base["long_strike"])
    row.update(rebuilt_feature_fields(obs))
    row.update(
        {
            "side": side,
            "dte_hours": base.get("dte_hours"),
            "short_distance_fraction": abs(short_strike - entry_price) / entry_price,
            "width_fraction": actual_width / entry_price,
            "side_sign": -1.0 if side == "put" else 1.0,
            "hour_sin": hour_sin(entry_ms),
            "hour_cos": hour_cos(entry_ms),
            "shock_magnitude": abs(float(markers["peak_m_die"])) if to_float(markers.get("peak_m_die")) is not None else None,
            "elapsed_from_shock_min": None,
        }
    )
    if row.get("adverse_move_since_shock") is None:
        row["adverse_move_since_shock"] = row.get("down_move_since_shock" if side == "put" else "up_move_since_shock")
    if row.get("favorable_move_since_shock") is None:
        row["favorable_move_since_shock"] = row.get("up_move_since_shock" if side == "put" else "down_move_since_shock")
    return row


def predict_models_for_row(models: dict[str, dict[str, Any]], model_row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    for label, spec in models.items():
        artifact = spec["artifact"]
        missing = missing_features_for(artifact, model_row)
        outside = outside_support_for(artifact, model_row)
        try:
            prediction = predict_row(artifact, model_row)
        except Exception as exc:  # pragma: no cover - exercised by integration if artifact corrupts
            predictions[label] = {
                "status": "error",
                "model_file": spec["file"],
                "model_version": spec.get("model_version"),
                "model_kind": spec.get("model_kind"),
                "feature_group": spec.get("feature_group"),
                "artifact_hash": spec.get("artifact_hash"),
                "error": str(exc),
                "missing_features": missing,
                "outside_training_support_features": outside,
            }
            continue
        predictions[label] = {
            "status": prediction.get("status"),
            "model_file": spec["file"],
            "model_version": prediction.get("model_version"),
            "model_kind": prediction.get("model_kind"),
            "feature_group": prediction.get("feature_group"),
            "training_cutoff": prediction.get("training_cutoff"),
            "artifact_hash": spec.get("artifact_hash"),
            "model_file_sha256": spec.get("sha256"),
            "prediction_hash": prediction.get("prediction_hash"),
            "probability_positive": prediction.get("probability_positive"),
            "conditional_positive_loss": prediction.get("conditional_positive_loss"),
            "expected_loss_normalized": prediction.get("expected_loss_normalized"),
            "expected_payout_btc": (
                float(prediction["expected_loss_normalized"]) * (float(model_row["actual_width"]) / float(model_row["entry_price"]))
                if prediction.get("expected_loss_normalized") is not None
                else None
            ),
            "missing_features": missing,
            "outside_training_support_features": outside,
        }
    return predictions


def finite(value: Any) -> float | None:
    return to_float(value)


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def summarize_model_predictions(rows: list[dict[str, Any]], model_labels: list[str]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    primary_rows = [row for row in rows if row.get("sample_weight") == 1 and finite(row.get("actual_loss_normalized")) is not None]
    for label in model_labels:
        errors: list[float] = []
        squared: list[float] = []
        predicted: list[float] = []
        actuals: list[float] = []
        probabilities: list[float] = []
        missing_counter = Counter()
        outside_counter = Counter()
        for row in primary_rows:
            pred = (row.get("predictions") or {}).get(label) or {}
            expected = finite(pred.get("expected_loss_normalized"))
            actual = finite(row.get("actual_loss_normalized"))
            if expected is None or actual is None:
                continue
            errors.append(abs(expected - actual))
            squared.append((expected - actual) ** 2)
            predicted.append(expected)
            actuals.append(actual)
            probability = finite(pred.get("probability_positive"))
            if probability is not None:
                probabilities.append(probability)
            missing_counter.update(pred.get("missing_features") or [])
            outside_counter.update(pred.get("outside_training_support_features") or [])
        by_model[label] = {
            "rows_scored_unique_identity": len(errors),
            "mae_loss_normalized": mean(errors),
            "rmse_loss_normalized": math.sqrt(mean(squared)) if squared else None,
            "mean_predicted_loss_normalized": mean(predicted),
            "mean_actual_loss_normalized": mean(actuals),
            "mean_probability_positive": mean(probabilities),
            "top_missing_features": dict(missing_counter.most_common(12)),
            "outside_training_support_features": dict(outside_counter.most_common(12)),
        }

    ranking: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in primary_rows:
        grouped[str(row.get("identity_key"))].append(row)
    for label in model_labels:
        comparable = matches = ties = 0
        for pair_rows in grouped.values():
            by_side = {str(row.get("side")): row for row in pair_rows}
            if "put" not in by_side or "call" not in by_side:
                continue
            put_pred = finite(((by_side["put"].get("predictions") or {}).get(label) or {}).get("expected_loss_normalized"))
            call_pred = finite(((by_side["call"].get("predictions") or {}).get(label) or {}).get("expected_loss_normalized"))
            put_actual = finite(by_side["put"].get("actual_loss_normalized"))
            call_actual = finite(by_side["call"].get("actual_loss_normalized"))
            if put_pred is None or call_pred is None or put_actual is None or call_actual is None:
                continue
            comparable += 1
            if abs(put_pred - call_pred) <= 1e-12:
                ties += 1
                continue
            predicted_side = "put" if put_pred < call_pred else "call"
            if abs(put_actual - call_actual) <= 1e-12:
                ties += 1
            else:
                actual_side = "put" if put_actual < call_actual else "call"
                if predicted_side == actual_side:
                    matches += 1
        ranking[label] = {
            "comparable_unique_identity_pairs": comparable,
            "lower_predicted_payout_side_matches_lower_actual_payout": matches,
            "ties_or_equal_actual_loss": ties,
            "match_rate_excluding_ties": matches / (comparable - ties) if comparable > ties else None,
        }
    return {"by_model": by_model, "side_risk_ranking": ranking}


def result_status_for_card(row_statuses: list[str]) -> str:
    if any(status == "settled" for status in row_statuses):
        return "has_settled_rows"
    if row_statuses:
        return row_statuses[0]
    return "not_attempted"


def build_nr_comparison(
    root: Path = DEFAULT_ROOT,
    nr_interface: Path | None = None,
    output_dir: Path | None = None,
    link_price_events: bool = True,
) -> dict[str, Any]:
    root = Path(root)
    nr_interface = Path(nr_interface) if nr_interface else root / "archives-analysis" / "nr_observation_interface.jsonl"
    output_dir = Path(output_dir) if output_dir else root / "nr-comparison"
    output_dir.mkdir(parents=True, exist_ok=True)

    observations = load_jsonl(nr_interface)
    native = native_rows(observations)
    identity_meta, duplicate_groups = build_identity_metadata(native)
    contracts, option_source_hash = load_contracts(root)
    delivery = load_delivery_prices(root)
    spot = SpotOpenCache(root)
    models = load_portable_models(root)

    native_entries = {
        int(((obs.get("reference_timing") or {}).get("entry_open_ms") or 0))
        for obs in native
        if (obs.get("reference_timing") or {}).get("entry_open_ms") is not None
    }
    overlaps_by_minute = load_price_event_overlaps(root, native_entries) if link_price_events else {}

    card_path = output_dir / "nr_card_ledger.csv"
    comparison_path = output_dir / "nr_side_width_comparison.csv"
    prediction_path = output_dir / "prediction_requests.jsonl"
    model_prediction_path = output_dir / "nr_model_predictions.jsonl"
    report_path = output_dir / "nr_comparison_report.md"
    summary_path = output_dir / "nr_comparison_summary.json"

    counts = Counter()
    row_status_counts = Counter()
    status_counts = Counter()
    rows_by_side = Counter()
    rows_by_width = Counter()
    rows_by_bucket = Counter()
    primary_rows_by_bucket = Counter()
    primary_settled_by_bucket = Counter()
    feature_availability = Counter()
    feature_availability_all_rows = Counter()
    primary_settled_records: set[str] = set()
    all_settled_records: set[str] = set()
    ordinary_rows = 0
    entry_price_rows = 0
    settlement_rows = 0
    ordinary_records: set[str] = set()
    entry_price_records: set[str] = set()
    settlement_records: set[str] = set()
    unique_card_ids: set[str] = set()
    prediction_rows = 0
    model_prediction_rows = 0
    model_prediction_records: list[dict[str, Any]] = []

    with card_path.open("w", encoding="utf-8", newline="") as card_file, comparison_path.open(
        "w", encoding="utf-8", newline=""
    ) as comparison_file, prediction_path.open("w", encoding="utf-8") as prediction_file, model_prediction_path.open(
        "w", encoding="utf-8"
    ) as model_prediction_file:
        card_writer = csv.DictWriter(card_file, fieldnames=CARD_LEDGER_FIELDS)
        comparison_writer = csv.DictWriter(comparison_file, fieldnames=COMPARISON_FIELDS)
        card_writer.writeheader()
        comparison_writer.writeheader()

        for native_sequence, obs in enumerate(native, 1):
            duplicate_meta = identity_meta[native_sequence]
            is_primary_identity = duplicate_meta.get("sample_weight") == 1
            identity = obs.get("identity") or {}
            timing = obs.get("reference_timing") or {}
            markers = obs.get("original_signal_markers") or {}
            source = obs.get("source") or {}
            availability = obs.get("availability_markers") or {}
            options = availability.get("options") or {}
            anchor = availability.get("anchor") or {}
            um_status = (obs.get("um_rebuilt_features") or {}).get("status")
            card_id = str(identity.get("card_id") or source.get("source_record_hash") or "unknown")
            record_key = str(duplicate_meta["identity_key"])
            unique_card_ids.add(card_id)
            counts["native_nr_rows"] += 1
            if is_primary_identity:
                counts["native_nr_unique_identity_records"] += 1
            if bool(anchor.get("available")):
                feature_availability_all_rows["anchor_available"] += 1
                if is_primary_identity:
                    feature_availability["anchor_available"] += 1
            if bool(options.get("structure_fields_present")):
                feature_availability_all_rows["option_structure_fields_present"] += 1
                if is_primary_identity:
                    feature_availability["option_structure_fields_present"] += 1
            if bool(options.get("field_level_options_factor")):
                feature_availability_all_rows["field_level_options_factor"] += 1
                if is_primary_identity:
                    feature_availability["field_level_options_factor"] += 1
            if options.get("strict_contract_usable") is True:
                feature_availability_all_rows["strict_contract_usable_true"] += 1
                if is_primary_identity:
                    feature_availability["strict_contract_usable_true"] += 1
            if bool(availability.get("near_term_market_context")):
                feature_availability_all_rows["near_term_market_context_available"] += 1
                if is_primary_identity:
                    feature_availability["near_term_market_context_available"] += 1
            if um_status == "available":
                feature_availability_all_rows["um_rebuilt_available"] += 1
                if is_primary_identity:
                    feature_availability["um_rebuilt_available"] += 1
            entry_ms_raw = timing.get("entry_open_ms")
            entry_ms = int(entry_ms_raw) if entry_ms_raw is not None else None
            expiry = expiry_ms(entry_ms) if entry_ms is not None else None
            dte_hours = (expiry - entry_ms) / 3_600_000 if entry_ms is not None and expiry is not None else None
            ordinary = bool(dte_hours is not None and MIN_DTE_HOURS < dte_hours <= MAX_DTE_HOURS)
            if ordinary:
                ordinary_rows += 1
                if is_primary_identity:
                    ordinary_records.add(record_key)
            else:
                counts["dte_excluded_rows"] += 1

            overlap_rows = overlaps_by_minute.get((entry_ms or -1) // MINUTE_MS, []) if entry_ms is not None else []
            overlap_status = "matched" if overlap_rows else "none"
            overlap_ids = compact_overlap_ids(overlap_rows)
            if overlap_rows:
                feature_availability_all_rows["price_event_same_minute_overlap"] += 1
                if is_primary_identity:
                    feature_availability["price_event_same_minute_overlap"] += 1
            entry_status = "not_checked"
            entry_price: float | None = None
            price_source = "Binance BTCUSDT spot next-minute open"
            if ordinary and entry_ms is not None:
                entry_status, entry_price, price_source = open_price_for(spot, entry_ms)
                if entry_price is not None:
                    entry_price_rows += 1
                    if is_primary_identity:
                        entry_price_records.add(record_key)
            delivery_date = delivery_date_for(expiry) if expiry is not None else None
            settlement_price = delivery.get(delivery_date) if delivery_date else None
            settlement_status = "available" if settlement_price is not None else "missing_official_delivery"
            if settlement_price is not None:
                settlement_rows += 1
                if is_primary_identity:
                    settlement_records.add(record_key)

            row_statuses: list[str] = []
            for side in SIDES:
                for target_width in WIDTHS:
                    bucket = comparison_bucket(markers.get("side_hint"), side)
                    base = {
                        "schema": SCHEMA,
                        "native_sequence": native_sequence,
                        "identity_key": duplicate_meta.get("identity_key"),
                        "identity_duplicate_role": duplicate_meta.get("identity_duplicate_role"),
                        "identity_duplicate_group_size": duplicate_meta.get("identity_duplicate_group_size"),
                        "identity_duplicate_relation": duplicate_meta.get("identity_duplicate_relation"),
                        "sample_weight": duplicate_meta.get("sample_weight"),
                        "row_id": canonical_hash(row_hash_parts(obs, native_sequence, side, int(target_width))),
                        "source_record_hash": source.get("source_record_hash"),
                        "card_id": identity.get("card_id"),
                        "confirmed_time_ms": identity.get("confirmed_time_ms"),
                        "entry_ms": entry_ms,
                        "expiry_ms": expiry,
                        "delivery_date": delivery_date,
                        "dte_hours": dte_hours,
                        "side": side,
                        "target_width": int(target_width),
                        "comparison_bucket": bucket,
                        "decision_lean": markers.get("decision_lean"),
                        "side_hint": markers.get("side_hint"),
                        "entry_price": entry_price,
                        "price_source": price_source,
                        "result_status": "pending",
                        "failure_reason": "",
                        "short_strike": None,
                        "long_strike": None,
                        "actual_width": None,
                        "short_name": None,
                        "long_name": None,
                        "short_creation_ms": None,
                        "long_creation_ms": None,
                        "option_source_sha256": option_source_hash,
                        "settlement_price": settlement_price,
                        "payout_btc": None,
                        "loss_normalized": None,
                        "payout_intrusion_at_breakeven_unknown_credit": "not_evaluated_without_net_credit",
                        "price_event_overlap_status": overlap_status,
                        "price_event_overlap_ids": overlap_ids,
                        "source_observation_hash": observation_hash(obs),
                        **rebuilt_feature_fields(obs),
                        **option_marker_fields(obs),
                        "anchor_available": to_bool_text(((obs.get("availability_markers") or {}).get("anchor") or {}).get("available")),
                    }
                    status = "settled"
                    failure_reason = ""
                    selected: tuple[dict[str, Any], dict[str, Any]] | None = None
                    if not ordinary:
                        status = "not_counted"
                        failure_reason = "dte_excluded"
                    elif entry_price is None or entry_ms is None:
                        status = "not_counted"
                        failure_reason = "entry_price_unavailable"
                    else:
                        expiry_contracts = contracts.get(int(expiry or 0), [])
                        if not expiry_contracts:
                            status = "not_counted"
                            failure_reason = "actual_expiry_contracts_unavailable"
                        else:
                            selected = select_legs(expiry_contracts, side, float(entry_price), int(target_width), int(entry_ms))
                            if selected is None:
                                status = "not_counted"
                                failure_reason = "actual_legs_unavailable"
                            elif settlement_price is None:
                                status = "not_counted"
                                failure_reason = "missing_official_delivery"
                    if selected is not None:
                        short, long = selected
                        actual_width = abs(float(short["strike"]) - float(long["strike"]))
                        base.update(
                            {
                                "short_strike": short.get("strike"),
                                "long_strike": long.get("strike"),
                                "actual_width": actual_width,
                                "short_name": short.get("instrument_name"),
                                "long_name": long.get("instrument_name"),
                                "short_creation_ms": short.get("creation_timestamp"),
                                "long_creation_ms": long.get("creation_timestamp"),
                            }
                        )
                        if status == "settled":
                            payment = payout(side, float(short["strike"]), float(long["strike"]), float(settlement_price))
                            loss_norm = payment / (actual_width / float(entry_price)) if actual_width and entry_price else None
                            base.update({"payout_btc": payment, "loss_normalized": loss_norm})
                    base["result_status"] = status
                    base["failure_reason"] = failure_reason
                    comparison_writer.writerow(base)
                    row_statuses.append(failure_reason or status)
                    row_status_counts[failure_reason or status] += 1
                    if is_primary_identity:
                        status_counts[failure_reason or status] += 1
                        rows_by_side[f"{side}:{failure_reason or status}"] += 1
                        rows_by_width[f"{target_width}:{failure_reason or status}"] += 1
                        rows_by_bucket[f"{bucket}:{failure_reason or status}"] += 1
                    if int(target_width) == PRIMARY_WIDTH:
                        if is_primary_identity:
                            primary_rows_by_bucket[bucket] += 1
                        if status == "settled" and is_primary_identity:
                            primary_settled_by_bucket[bucket] += 1
                            primary_settled_records.add(record_key)
                    if status == "settled":
                        if is_primary_identity:
                            all_settled_records.add(record_key)
                        if int(target_width) == PRIMARY_WIDTH:
                            prediction_request_id = canonical_hash(["native_nr_prediction_request_v1", base["row_id"]])
                            prediction = {
                                "schema": PREDICTION_SCHEMA,
                                "native_sequence": native_sequence,
                                "identity_key": duplicate_meta.get("identity_key"),
                                "identity_duplicate_role": duplicate_meta.get("identity_duplicate_role"),
                                "sample_weight": duplicate_meta.get("sample_weight"),
                                "request_id": prediction_request_id,
                                "row_id": base["row_id"],
                                "source_record_hash": base["source_record_hash"],
                                "card_id": base["card_id"],
                                "entry_ms": entry_ms,
                                "expiry_ms": expiry,
                                "side": side,
                                "target_width": int(target_width),
                                "actual_width": base["actual_width"],
                                "entry_price": entry_price,
                                "prediction_status": "awaiting_model_artifact",
                                "model_required": "astra_joint_two_part_payout_model@pending",
                                "feature_scope": "native_nr_card_time_features_only; no future outcomes, no historical LLM text",
                                "source_observation_hash": base["source_observation_hash"],
                            }
                            prediction_file.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
                            prediction_rows += 1
                            model_row = model_input_row(obs, base, side)
                            model_predictions = predict_models_for_row(models, model_row) if models else {}
                            model_prediction = {
                                "schema": MODEL_PREDICTION_SCHEMA,
                                "native_sequence": native_sequence,
                                "identity_key": duplicate_meta.get("identity_key"),
                                "identity_duplicate_role": duplicate_meta.get("identity_duplicate_role"),
                                "identity_duplicate_group_size": duplicate_meta.get("identity_duplicate_group_size"),
                                "identity_duplicate_relation": duplicate_meta.get("identity_duplicate_relation"),
                                "sample_weight": duplicate_meta.get("sample_weight"),
                                "prediction_request_id": prediction_request_id,
                                "row_id": base["row_id"],
                                "source_record_hash": base["source_record_hash"],
                                "card_id": base["card_id"],
                                "entry_ms": entry_ms,
                                "expiry_ms": expiry,
                                "delivery_date": delivery_date,
                                "side": side,
                                "target_width": int(target_width),
                                "actual_width": base["actual_width"],
                                "entry_price": entry_price,
                                "actual_payout_btc": base.get("payout_btc"),
                                "actual_loss_normalized": base.get("loss_normalized"),
                                "domain_status": "native_nr_domain_shift_from_price_rebalance_training_family",
                                "qualification_status": "development_projection_only_not_nr_model_qualification",
                                "quote_status": "not_evaluated_without_historical_net_credit",
                                "predictions": model_predictions,
                                "source_observation_hash": base["source_observation_hash"],
                            }
                            model_prediction_file.write(json.dumps(model_prediction, ensure_ascii=False, sort_keys=True) + "\n")
                            model_prediction_records.append(model_prediction)
                            model_prediction_rows += 1

            card_fields = card_base_fields(obs, native_sequence, duplicate_meta, entry_ms, expiry, dte_hours)
            primary_failure = result_status_for_card(row_statuses)
            if ordinary and entry_price is None:
                primary_failure = "entry_price_unavailable"
            elif ordinary and settlement_price is None and primary_failure == "missing_official_delivery":
                primary_failure = "missing_official_delivery"
            card_fields.update(
                {
                    "entry_price_status": entry_status,
                    "entry_price": entry_price,
                    "entry_price_source": price_source,
                    "settlement_status": settlement_status,
                    "settlement_price": settlement_price,
                    "price_event_overlap_status": overlap_status,
                    "price_event_overlap_ids": overlap_ids,
                    "primary_failure_reason": primary_failure,
                }
            )
            card_writer.writerow(card_fields)

    summary = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "nr_interface": str(nr_interface),
        "output_dir": str(output_dir),
        "constraints": {
            "training_used": False,
            "llm_called": False,
            "production_sidecars_written": False,
            "reads_2023_outcomes": False,
            "ordinary_dte_hours": {"lower_open": MIN_DTE_HOURS, "upper_closed": MAX_DTE_HOURS},
            "entry_basis": "original card confirmation next-minute spot open",
            "expiry_basis": "nearest Beijing 16:00 after simulated entry",
            "result_basis": "official Deribit delivery price only",
            "model_prediction_basis": "sealed portable artifacts applied to native NR card-time features only",
            "nr_prediction_domain_status": "development projection; native NR is outside the price-rebalance training event family",
        },
        "counts": {
            **dict(counts),
            "input_rows": len(observations),
            "native_nr_cards": len(native),
            "native_nr_rows": len(native),
            "native_nr_unique_identities": len(identity_meta) - sum(1 for item in identity_meta.values() if item.get("sample_weight") == 0),
            "unique_card_ids": len(unique_card_ids),
            "duplicate_identity_groups": len(duplicate_groups),
            "duplicate_identity_rows": sum(max(0, int(group["count"]) - 1) for group in duplicate_groups),
            "ordinary_8_24h_rows": ordinary_rows,
            "ordinary_8_24h_cards": len(ordinary_records),
            "ordinary_8_24h_unique_identities": len(ordinary_records),
            "entry_price_available_rows": entry_price_rows,
            "entry_price_available_cards": len(entry_price_records),
            "settlement_available_rows": settlement_rows,
            "settlement_available_cards": len(settlement_records),
            "cards_with_any_settled_row": len(all_settled_records),
            "cards_with_primary_width_settled_row": len(primary_settled_records),
            "comparison_rows": sum(row_status_counts.values()),
            "comparison_rows_unique_identity": sum(status_counts.values()),
            "prediction_request_rows": prediction_rows,
            "model_prediction_rows": model_prediction_rows,
        },
        "status_counts": dict(status_counts),
        "row_status_counts_all_rows": dict(row_status_counts),
        "rows_by_side_status": dict(rows_by_side),
        "rows_by_width_status": dict(rows_by_width),
        "rows_by_bucket_status": dict(rows_by_bucket),
        "primary_width_rows_by_bucket": dict(primary_rows_by_bucket),
        "primary_width_settled_rows_by_bucket": dict(primary_settled_by_bucket),
        "feature_availability_records": dict(feature_availability),
        "feature_availability_all_rows": dict(feature_availability_all_rows),
        "duplicate_identity_groups": duplicate_groups,
        "portable_models": {
            label: {
                key: spec.get(key)
                for key in ("file", "sha256", "artifact_hash", "feature_group", "model_version", "model_kind", "training_cutoff")
            }
            for label, spec in models.items()
        },
        "model_prediction_summary": summarize_model_predictions(model_prediction_records, list(models.keys())) if models else {},
        "source_hashes": {
            "nr_interface_sha256": digest(nr_interface),
            "deribit_instruments_sha256": option_source_hash,
            "deribit_delivery_prices_sha256": digest(root / "raw" / "deribit" / "delivery_prices.json"),
        },
    }

    report = [
        "# 原生 NR 参考两腿与赔付对照",
        "",
        f"- 原生 NR 账本行：{len(native)} 行；去重身份：{len(identity_meta) - sum(1 for item in identity_meta.values() if item.get('sample_weight') == 0)} 个。",
        f"- 重复身份组：{len(duplicate_groups)} 组，主统计不把重复行当独立样本。",
        f"- 普通轮资格：原始行 {ordinary_rows} 行，去重身份 {len(ordinary_records)} 个；口径为 8 小时 < 入场至北京时间16:00到期 ≤ 24 小时。",
        f"- 主宽度 {PRIMARY_WIDTH} 美元存在已结算结果的卡：{len(primary_settled_records)} 张；任一宽度任一侧存在已结算结果的卡：{len(all_settled_records)} 张。",
        f"- 对照行：全量 {sum(row_status_counts.values())} 行，去重主统计 {sum(status_counts.values())} 行。",
        f"- 预测请求占位：{prediction_rows} 行；真实封存模型离线预测：{model_prediction_rows} 行。",
        "",
        "本输出只用于开发对照：原方向、相反侧与中性两侧分别保留；UM 重建特征和期权结构字段只是卡前可用标记，不被写成新的价格事件或训练样本。封存模型预测属于 NR 域外映射，不声明 NR 预测资格已经成立。",
        "",
        "## 失败原因分布",
        "",
    ]
    for key, value in sorted(status_counts.items()):
        report.append(f"- {key}: {value}")
    if duplicate_groups:
        report.extend(["", "## 重复身份", ""])
        for group in duplicate_groups:
            report.append(
                f"- {group['identity_key']}: {group['count']} 行，序号 {group['native_sequences']}，关系 {group['relation']}。"
            )
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")

    file_hashes = {
        "nr_card_ledger.csv": digest(card_path),
        "nr_side_width_comparison.csv": digest(comparison_path),
        "prediction_requests.jsonl": digest(prediction_path),
        "nr_model_predictions.jsonl": digest(model_prediction_path),
        "nr_comparison_report.md": digest(report_path),
    }
    summary["files"] = file_hashes
    summary["result_bundle_sha256"] = hashlib.sha256(
        json.dumps(file_hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build native NR reference spread and payout comparison")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--nr-interface", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-price-event-links", action="store_true")
    args = parser.parse_args()
    summary = build_nr_comparison(
        root=args.root,
        nr_interface=args.nr_interface,
        output_dir=args.output_dir,
        link_price_events=not args.no_price_event_links,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
