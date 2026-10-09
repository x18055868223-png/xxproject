"""Offline WPEM native-input qualification; never renew sources or grant gates."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from . import astra_underwriting_fact_context_v20 as facts
else:
    import astra_candidate_asof_v1 as packets
    import astra_underwriting_fact_context_v20 as facts

SCHEMA = "wpem_native_qualification@1.1.0-shadow"
REQUIRED_NATIVE_PRODUCER_VERSION = "2.0.2"
WINDOW_SCHEMA = "wpem_qualified_window@1.0.0-shadow"
MAX_GAP_MS = 600_000
WINDOWS_MS = (900_000, 3_600_000)


def seal(body, key):
    return {**deepcopy(body), key: packets.core._hash(body)}


def require_hash(value, key):
    if value.get(key) != packets.core._hash({k: v for k, v in value.items() if k != key}):
        raise ValueError("wpem_hash_mismatch:" + key)


def _maps(value):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _maps(child)
    elif isinstance(value, list):
        for child in value:
            yield from _maps(child)


def _reject_json_constant(value):
    raise ValueError("non_json_numeric_constant:" + value)


def qualify_native(packet: Mapping[str, Any], native_raw: bytes | None = None,
                   *, evidence_kind: str = "historical_public_capture",
                   acquisition_receipt: Mapping[str, Any] | None = None) -> dict:
    """Receipts stay separate; missing producer clocks are never backfilled."""
    replay = packets.replay_packet(packet)
    snapshot = packet["snapshot"]
    cutoff = packet["cutoff"]["decision_asof_ms"]
    gaps = []
    raw_hash, producer, decision, clock_values = None, {}, {}, {}
    if native_raw is None:
        gaps.append("NATIVE_SOURCE_BYTES_MISSING")
    else:
        if not isinstance(native_raw, bytes):
            gaps.append("NATIVE_SOURCE_BYTES_MALFORMED")
        else:
            raw_hash = hashlib.sha256(native_raw.rstrip(b"\r\n")).hexdigest()
            try:
                producer = json.loads(native_raw, parse_constant=_reject_json_constant)
            except (ValueError, UnicodeError):
                gaps.append("NATIVE_SOURCE_BYTES_MALFORMED")
        if not isinstance(producer, Mapping):
            gaps.append("NATIVE_SOURCE_BYTES_MALFORMED")
            producer = {}
        decision = producer.get("decision")
        if not isinstance(decision, Mapping):
            gaps.append("NATIVE_DECISION_MALFORMED")
            decision = {}
        runtime = decision.get("runtime_facts")
        if not isinstance(runtime, Mapping):
            gaps.append("NATIVE_RUNTIME_FACTS_MALFORMED")
            runtime = {}
        if producer.get("schema_name") != "EvaluationSnapshot":
            gaps.append("NATIVE_EVALUATION_SCHEMA_MISMATCH")
        if producer.get("schema_version") != "nrd.schema.v1.0.0":
            gaps.append("NATIVE_SCHEMA_VERSION_MISMATCH")
        if decision.get("demo_version") != REQUIRED_NATIVE_PRODUCER_VERSION:
            gaps.append("NATIVE_PRODUCER_VERSION_MISMATCH")
        if decision.get("symbol") != "BTC":
            gaps.append("NATIVE_UNDERLYING_MISMATCH")
        if runtime.get("runtime_mode") != "live_public_read_only":
            gaps.append("NATIVE_RUNTIME_IDENTITY_MISMATCH")
        if any(row.get(flag) is True for row in _maps(producer)
               for flag in ("compat_backfill_applied", "synthetic", "synthetic_preview", "reconstructed")):
            gaps.append("NATIVE_COMPAT_OR_SYNTHETIC_REJECTED")
        current_context = producer.get("current_snapshot_context")
        current_context = current_context if isinstance(current_context, Mapping) else {}
        session = current_context.get("session_context")
        session = session if isinstance(session, Mapping) else {}
        if (session.get("schema") != "SignalSessionPremiseDurabilityContext@1.0.0"
                or any(session.get(key) is None for key in
                       ("clock_window", "backtest_delta_pp", "validation_basis"))):
            gaps.append("NATIVE_SESSION_SCHEMA_MISSING")
        if (current_context.get("temporal_durability") is None
                or current_context.get("temporal_durability") != session.get("premise_durability")):
            gaps.append("NATIVE_TEMPORAL_DURABILITY_MISSING")
        if (current_context.get("schema") != "current_evaluation_context@1.0.0"
                or current_context.get("source") != "current_evaluation_snapshot"
                or current_context.get("observed_at_ms") != decision.get("ts_ms")
                or session.get("observed_at_ms") != decision.get("ts_ms")
                or current_context.get("phase") != "PHASE_0_OBSERVE_ONLY"
                or any(current_context.get(flag) is not False for flag in
                       ("affects_confidence", "affects_blocking", "affects_trade_allowed"))):
            gaps.append("NATIVE_CURRENT_CONTEXT_BINDING_INVALID")
        provenance = producer.get("clock_provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        expected_provenance = {
            "schema": "native_snapshot_clock_provenance@1.0.0",
            "source_available_at_ms": "snapshot_payload_handoff_before_export_envelope",
            "ingested_at_ms": "producer_snapshot_exporter_acceptance",
            "snapshot_exported_ms": "producer_export_attempt_before_atomic_write",
            "not_remote_receiver_ingestion": True,
            "not_filesystem_visibility_proof": True,
            "not_market_source_ingestion": True,
        }
        if any((provenance.get(key) is not value if isinstance(value, bool)
                else provenance.get(key) != value) for key, value in expected_provenance.items()):
            gaps.append("NATIVE_CLOCK_PROVENANCE_INVALID")
        export_ms, ingestion_ms = producer.get("snapshot_exported_ms"), producer.get("ingested_at_ms")
        if (not isinstance(export_ms, int) or isinstance(export_ms, bool)
                or not isinstance(ingestion_ms, int) or isinstance(ingestion_ms, bool)
                or not 0 <= ingestion_ms <= export_ms <= cutoff):
            gaps.append("NATIVE_EXPORT_CLOCK_INVALID")
        clock_values = {"observation_time": decision.get("ts_ms"),
                        "source_available_at": producer.get("source_available_at_ms"),
                        "ingested_at": producer.get("ingested_at_ms"),
                        "decision_asof": cutoff}
        for name, value in clock_values.items():
            if value is None:
                gaps.append("NATIVE_CLOCK_MISSING:" + name)
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                gaps.append("NATIVE_CLOCK_INVALID:" + name)
            elif value > cutoff:
                gaps.append("NATIVE_FUTURE_CLOCK:" + name)
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
               for value in clock_values.values()):
            values = list(clock_values.values())
            if values != sorted(values):
                gaps.append("NATIVE_CLOCK_ORDER_INVALID")
        fact = snapshot["fact_context"]
        if fact.get("source_hash") != raw_hash:
            gaps.append("NATIVE_FACT_SOURCE_HASH_MISMATCH")
        try:
            projected = facts.from_evaluation_snapshot(producer, cutoff, source_hash=raw_hash)
            if any(fact.get(key) != projected.get(key) for key in (
                    "snapshot_ts_ms", "producer_schema_version", "underlying", "strategy_version",
                    "runtime_mode", "current_price_usd", "current_price_source",
                    "price_observed_ms", "market_facts")):
                gaps.append("NATIVE_FACT_CONTENT_MISMATCH")
        except ValueError:
            gaps.append("NATIVE_FACT_PROJECTION_REJECTED")
        if not any((row.get("identity") or {}).get("product_id") == "fmz.bridge.snapshots.v1"
                   and ((row.get("content") or {}).get("values") or {}).get("source_hash") == raw_hash
                   for row in packets._source_records(snapshot).values()):
            gaps.append("NATIVE_MANIFEST_SOURCE_BINDING_MISSING")
        if fact.get("snapshot_ts_ms") != decision.get("ts_ms"):
            gaps.append("NATIVE_FACT_TIME_MISMATCH")
        if fact.get("strategy_version") != REQUIRED_NATIVE_PRODUCER_VERSION or fact.get("status") != "current":
            gaps.append("NATIVE_FACT_NOT_CURRENT_AT_ORIGINAL_ASOF")
    if evidence_kind not in {"native_producer", "synthetic_fixture"}:
        gaps.append("NATIVE_PROVENANCE_NOT_ATTESTED")
    if evidence_kind == "native_producer" and packet["provenance"]["source_kind"] != "live_public_capture":
        gaps.append("NATIVE_PACKET_PROVENANCE_MISMATCH")
    if not packet["sources"]["record_ids"] or not packet["versions"]["source_manifest_hashes"]:
        gaps.append("NATIVE_FROZEN_MANIFEST_MISSING")
    if replay["raw_quote_status"] != "raw_leg_books_replayed":
        gaps.append("NATIVE_TWO_LEG_BOOKS_MISSING")
    if snapshot["market"]["quote"]["gap_reasons"] or snapshot["fee"]["amount_btc"] is None:
        gaps.append("NATIVE_QUOTE_OR_FEE_UNQUALIFIED")
    if "original_instrument_metadata_receipt_clock_not_retained" in packet["gaps"]:
        gaps.append("NATIVE_METADATA_RECEIPT_MISSING")
    if acquisition_receipt is not None and not isinstance(acquisition_receipt, Mapping):
        gaps.append("ACQUISITION_RECEIPT_MALFORMED")
        receipt = {}
    else:
        receipt = deepcopy(dict(acquisition_receipt or {}))
        try:
            packets.core._hash(receipt)
        except (TypeError, ValueError):
            gaps.append("ACQUISITION_RECEIPT_MALFORMED")
            receipt = {}
    for key in ("retrieved_at_ms", "ingested_at_ms"):
        value = receipt.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            gaps.append("RECEIPT_CLOCK_INVALID:" + key)
        elif value is not None and value > cutoff:
            gaps.append("RECEIPT_OBTAINED_AFTER_ORIGINAL_ASOF:" + key)
    shape_passed = not gaps
    status = ("NATIVE_QUALIFIED" if shape_passed and evidence_kind == "native_producer" else
              "SYNTHETIC_CONTRACT_PASSED" if shape_passed else "BLOCKED")
    return seal({"schema": SCHEMA, "status": status, "contract_qualified": shape_passed,
                 "candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
                 "packet_hash": packet["packet_hash"], "decision_asof_ms": cutoff,
                 "evidence_kind": evidence_kind, "source_hash": raw_hash,
                 "original_native_clocks": clock_values, "supplemental_acquisition_receipt": receipt,
                 "producer_version": decision.get("demo_version"),
                 "producer_schema": producer.get("schema_version"),
                 "comparability_hash": packets.core._hash({
                     "candidate_window": packet["window"]["comparability_hash"],
                     "producer_version": decision.get("demo_version"),
                     "producer_schema": producer.get("schema_version"), "method": SCHEMA}),
                 "gaps": sorted(set(gaps)), "original_packet_gaps": packet["gaps"],
                 "current_at_export": False, "execution_allowed": False}, "qualification_hash")


def qualified_windows(reports: Sequence[Mapping[str, Any]], candidate_id: str, cutoff_ms: int) -> dict:
    cutoff_ms = packets._clock(cutoff_ms)
    same = []
    for report in reports:
        require_hash(report, "qualification_hash")
        if report["candidate_id"] != candidate_id:
            continue
        if report["decision_asof_ms"] > cutoff_ms:
            raise ValueError("future_qualified_window_point")
        same.append(report)
    same.sort(key=lambda row: row["decision_asof_ms"])
    latest = same[-1] if same else None
    # Reject conflicting duplicate times and preserve the newest method boundary.
    seen = {}
    for row in same:
        ts = row["decision_asof_ms"]
        if ts in seen and seen[ts] != row["qualification_hash"]:
            raise ValueError("conflicting_qualified_window_point")
        seen[ts] = row["qualification_hash"]
    same = list({row["snapshot_id"]: row for row in same}.values())
    boundary = max((i + 1 for i, row in enumerate(same)
                    if latest and row["comparability_hash"] != latest["comparability_hash"]), default=0)
    same = same[boundary:]
    windows = []
    for duration in WINDOWS_MS:
        start = cutoff_ms - duration
        anchor = next((row for row in reversed(same) if row["decision_asof_ms"] <= start), None)
        selected = ([anchor] if anchor else []) + [row for row in same if start < row["decision_asof_ms"] <= cutoff_ms]
        times = [row["decision_asof_ms"] for row in selected]
        max_gap = max([b-a for a,b in zip(times, times[1:])] + ([cutoff_ms-times[-1]] if times else []), default=0)
        real = sum(row["status"] == "NATIVE_QUALIFIED" for row in selected)
        qualified = sum(row["contract_qualified"] is True for row in selected)
        actual_span = max(times)-min(times) if times else 0
        covered = bool(anchor and qualified == len(selected) and qualified >= 3
                       and max_gap <= MAX_GAP_MS and actual_span >= duration)
        kind = "native_producer" if selected and real == len(selected) else "synthetic_fixture" if (
            selected and all(row["evidence_kind"] == "synthetic_fixture" for row in selected)) else "unqualified_or_mixed"
        status = ("NATIVE_WINDOW_QUALIFIED" if covered and kind == "native_producer" else
                  "SYNTHETIC_WINDOW_COVERED" if covered and kind == "synthetic_fixture" else "INSUFFICIENT_WINDOW")
        windows.append({"requested_window_ms": duration, "coverage_start_ms": min(times) if times else None,
                        "coverage_end_ms": max(times) if times else None, "cutoff_ms": cutoff_ms,
                        "actual_coverage_ms": actual_span,
                        "qualified_points": qualified, "native_qualified_points": real,
                        "point_count": len(selected), "max_gap_ms": max_gap, "status": status,
                        "evidence_kind": kind, "point_refs": [
                            {key: row[key] for key in ("snapshot_id", "qualification_hash", "decision_asof_ms")}
                            for row in selected]})
    return seal({"schema": WINDOW_SCHEMA, "candidate_id": candidate_id, "cutoff_ms": cutoff_ms,
                 "segment_id": packets.core._hash({"candidate_id": candidate_id,
                     "comparison": latest["comparability_hash"] if latest else None,
                     "start": same[0]["decision_asof_ms"] if same else None})[:24],
                 "method_break_observed": bool(boundary), "windows": windows,
                 "independent_trade_count": None, "execution_allowed": False}, "window_hash")
