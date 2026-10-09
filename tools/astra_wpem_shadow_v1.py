"""Shadow-only WPEM window evidence and bounded two-question projection.

This module consumes frozen packets, native-window reports, and B-segment
reference diagnostics. It never calls an LLM, never scores a model, and never
changes production review gates.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from . import astra_underwriting_review_v20 as legacy_review
    from . import astra_wpem_native_v1 as native
else:
    import astra_candidate_asof_v1 as packets
    import astra_underwriting_review_v20 as legacy_review
    import astra_wpem_native_v1 as native

WINDOW_EVIDENCE_SCHEMA = "wpem_window_evidence@1.0.0-shadow"
PROJECTION_SCHEMA = "wpem_shadow_projection@1.0.0"
CLAIM_CATALOG_SCHEMA = "wpem_shadow_claim_catalog@1.0.0"
SELECTION_SCHEMA = "wpem_shadow_selection@1.0.0"
Q1_Q2_SCHEMA = "wpem_shadow_q1_q2@1.0.0"
RESPONSE_PROTOCOL = "bounded_claim_ids@2.5.0-wpem-shadow"
MAX_SELECTED_IDS = 8

WIRE_RESPONSE_KEYS = {
    "response_protocol",
    "reviewed_snapshot_id",
    "projection_hash",
    "claim_catalog_hash",
    "applicability",
    "selected_claim_ids",
    "selected_counter_ids",
    "selected_gap_ids",
    "selected_reassess_condition_ids",
}


class ShadowValidationError(ValueError):
    pass


def _hash(value: Any) -> str:
    return packets.core._hash(value)


def _seal(body: Mapping[str, Any], key: str) -> dict[str, Any]:
    data = dict(deepcopy(body))
    data[key] = _hash(data)
    return data


def _require_hash(value: Mapping[str, Any], key: str) -> None:
    native.require_hash(value, key)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _current_packet(
    packet_sequence: Sequence[Mapping[str, Any]],
    candidate_id: str | None,
    cutoff_ms: int | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    by_snapshot: dict[str, dict[str, Any]] = {}
    candidates = []
    for packet in packet_sequence:
        packets.replay_packet(packet)
        snapshot = packet["snapshot"]
        row = dict(deepcopy(packet))
        sid = snapshot["snapshot_id"]
        if sid in by_snapshot and by_snapshot[sid]["packet_hash"] != packet["packet_hash"]:
            raise ValueError("window_packet_snapshot_hash_conflict")
        by_snapshot[sid] = row
        if candidate_id is None or snapshot["candidate_id"] == candidate_id:
            candidates.append(row)
    if not candidates:
        raise ValueError("window_candidate_packet_missing")
    candidates.sort(key=lambda packet: packet["cutoff"]["decision_asof_ms"])
    if cutoff_ms is None:
        current = candidates[-1]
    else:
        cutoff_ms = packets._clock(cutoff_ms)
        matches = [packet for packet in candidates if packet["cutoff"]["decision_asof_ms"] == cutoff_ms]
        if not matches:
            raise ValueError("window_cutoff_packet_missing")
        current = matches[-1]
    return current, by_snapshot


def _bind_reports(
    reports: Sequence[Mapping[str, Any]],
    by_snapshot: Mapping[str, Mapping[str, Any]],
    native_sources: Mapping[str, bytes] | None = None,
) -> dict[str, dict[str, Any]]:
    by_hash: dict[str, dict[str, Any]] = {}
    for report in reports:
        _require_hash(report, "qualification_hash")
        sid = report.get("snapshot_id")
        if sid not in by_snapshot:
            raise ValueError("window_report_packet_missing")
        packet = by_snapshot[str(sid)]
        if report.get("packet_hash") != packet.get("packet_hash"):
            raise ValueError("window_report_packet_hash_mismatch")
        if report.get("candidate_id") != packet["snapshot"]["candidate_id"]:
            raise ValueError("window_report_candidate_mismatch")
        if report.get("decision_asof_ms") != packet["cutoff"]["decision_asof_ms"]:
            raise ValueError("window_report_asof_mismatch")
        # A self-hash proves content identity, not authority to upgrade its source.
        packet_kind = (packet.get("provenance") or {}).get("source_kind")
        report_kind = report.get("evidence_kind")
        status = report.get("status")
        # Frozen 1.0 reports remain readable only as their original BLOCKED
        # conclusion. New native/synthetic qualification requires 1.1.
        legacy_blocked = (report.get("schema") == "wpem_native_qualification@1.0.0-shadow"
                          and status == "BLOCKED"
                          and report.get("contract_qualified") is False
                          and report_kind != "native_producer")
        if (report.get("schema") != native.SCHEMA and not legacy_blocked) or status not in {
                "NATIVE_QUALIFIED", "SYNTHETIC_CONTRACT_PASSED", "BLOCKED"}:
            raise ValueError("window_report_schema_or_status_invalid")
        if status == "BLOCKED" and report.get("contract_qualified") is not False:
            raise ValueError("window_report_blocked_status_contract_mismatch")
        if report_kind == "native_producer" and packet_kind != "live_public_capture":
            raise ValueError("window_report_native_provenance_mismatch")
        if status == "NATIVE_QUALIFIED":
            if report_kind != "native_producer" or packet_kind != "live_public_capture":
                raise ValueError("window_report_native_status_provenance_mismatch")
            if report.get("contract_qualified") is not True or report.get("gaps") != []:
                raise ValueError("window_report_native_status_contract_mismatch")
            source_bytes = (native_sources or {}).get(report.get("source_hash"))
            if not isinstance(source_bytes, bytes):
                raise ValueError("window_report_native_source_bytes_missing")
            expected = native.qualify_native(
                packet, source_bytes, evidence_kind="native_producer",
                acquisition_receipt=report.get("supplemental_acquisition_receipt"),
            )
            if expected != report:
                raise ValueError("window_report_native_requalification_mismatch")
        if status == "SYNTHETIC_CONTRACT_PASSED":
            if report_kind != "synthetic_fixture":
                raise ValueError("window_report_synthetic_status_kind_mismatch")
            if report.get("contract_qualified") is not True or report.get("gaps") != []:
                raise ValueError("window_report_synthetic_status_contract_mismatch")
        by_hash[str(report["qualification_hash"])] = dict(deepcopy(report))
    return by_hash


def _level(packet: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = packet["snapshot"]
    contract = snapshot["contract"]
    market = snapshot["market"]
    economics = snapshot["economics"]
    liability = snapshot["liability"]
    fact = snapshot.get("fact_context") if isinstance(snapshot.get("fact_context"), Mapping) else {}
    facts = fact.get("market_facts") if isinstance(fact.get("market_facts"), Mapping) else {}
    anchor_group = {
        "space_gate": deepcopy(facts.get("space_gate") or (facts.get("space_context") or {}).get("space_gate")),
        "anchor": deepcopy(facts.get("anchor") or facts.get("price_anchor")),
        "gex": deepcopy(facts.get("gex") or facts.get("gamma") or facts.get("gex_info")),
        "wall": deepcopy(facts.get("wall") or facts.get("gamma_wall")),
    }
    gamma_regime = (
        facts.get("gamma_regime")
        or (facts.get("gex_info") or {}).get("gamma_regime")
        or (facts.get("gamma") or {}).get("regime")
        or (facts.get("gex") or {}).get("regime")
    )
    present_group = sorted(key for key, value in anchor_group.items() if value)
    cutoff = packet["cutoff"]["decision_asof_ms"]
    return {
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "decision_asof_ms": cutoff,
        "side": contract.get("side"),
        "quantity_btc": contract.get("quantity_btc"),
        "short_strike_usd": contract.get("short_strike_usd"),
        "long_strike_usd": contract.get("long_strike_usd"),
        "width_usd": contract.get("width_usd"),
        "expiry_ms": contract.get("expiry_ms"),
        "dte_ms": max(int(contract["expiry_ms"]) - cutoff, 0),
        "reference_price_usd": market.get("reference_price_usd"),
        "reference_source": market.get("reference_source"),
        "visible_credit_btc": economics.get("visible_credit_btc"),
        "fee_btc": snapshot.get("fee", {}).get("amount_btc"),
        "loss_budget_btc": economics.get("loss_budget_btc"),
        "mu_ref_btc": economics.get("mu_ref_btc"),
        "M_ref_btc": economics.get("reference_margin_btc"),
        "signed_distance_to_short_pct": liability.get("signed_distance_to_short_pct"),
        "signed_distance_to_long_pct": liability.get("signed_distance_to_long_pct"),
        "breakeven_usd": deepcopy(liability.get("breakeven_usd")),
        "anchor_gex_wall_group": {
            "present_inputs": present_group,
            "evidence_group": "anchor_gex_wall_dependent_group",
            "independent_confirmation_count": 1 if present_group else 0,
            "details": {key: value for key, value in anchor_group.items() if value},
        },
        "gamma_regime": {
            "value": deepcopy(gamma_regime),
            "source_fact_hash": _hash(gamma_regime) if gamma_regime is not None else None,
            "method_change_inference": None,
            "spot_anchor_exact_identity": False,
        },
    }


def _synthetic_account_fixture(packet: Mapping[str, Any]) -> bool:
    risk = packet["snapshot"].get("risk") if isinstance(packet["snapshot"].get("risk"), Mapping) else {}
    economics = packet["snapshot"].get("economics") if isinstance(packet["snapshot"].get("economics"), Mapping) else {}
    return (
        risk.get("source_kind") == "synthetic_fixture"
        and risk.get("status") == "supported"
        and economics.get("mu_ref_btc") is not None
        and economics.get("reference_margin_btc") is not None
    )


def _delta(first: Mapping[str, Any], last: Mapping[str, Any]) -> dict[str, Any]:
    first_level = _level(first)
    last_level = _level(last)
    fields = (
        "reference_price_usd",
        "visible_credit_btc",
        "loss_budget_btc",
        "signed_distance_to_short_pct",
        "signed_distance_to_long_pct",
        "dte_ms",
    )
    changes = {}
    for field in fields:
        a = _number(first_level.get(field))
        b = _number(last_level.get(field))
        changes[field + "_delta"] = None if a is None or b is None else b - a
    synthetic_same_basis = _synthetic_account_fixture(first) and _synthetic_account_fixture(last)
    if synthetic_same_basis:
        for field in ("mu_ref_btc", "M_ref_btc"):
            a = _number(first_level.get(field))
            b = _number(last_level.get(field))
            changes[field + "_delta"] = None if a is None or b is None else b - a
    else:
        changes["mu_ref_btc_delta"] = None
        changes["M_ref_btc_delta"] = None
    return {
        "status": "STRICT_SAME_CANDIDATE_DELTA",
        "from_snapshot_id": first["snapshot"]["snapshot_id"],
        "to_snapshot_id": last["snapshot"]["snapshot_id"],
        "from_decision_asof_ms": first["cutoff"]["decision_asof_ms"],
        "to_decision_asof_ms": last["cutoff"]["decision_asof_ms"],
        "changes": changes,
        "strict_same_definition": True,
        "synthetic_account_fixture_same_basis": synthetic_same_basis,
    }


def _zone(side: Any, price: float | None, short: float | None, long: float | None) -> str:
    if price is None or short is None or long is None:
        return "unknown"
    if side == "put":
        if price > short:
            return "outside_liability"
        if price > long:
            return "liability_band"
        return "protected_tail"
    if side == "call":
        if price < short:
            return "outside_liability"
        if price < long:
            return "liability_band"
        return "protected_tail"
    return "unknown"


def _path(points: Sequence[Mapping[str, Any]], window: Mapping[str, Any]) -> dict[str, Any]:
    if window.get("status") not in {"NATIVE_WINDOW_QUALIFIED", "SYNTHETIC_WINDOW_COVERED"}:
        return {
            "status": "NO_PATH_DUE_TO_WINDOW_GAP",
            "classification": "gap",
            "reason": "qualified_window_not_covered",
            "win_probability": None,
        }
    prices = [_number(point["snapshot"]["market"].get("reference_price_usd")) for point in points]
    side = points[-1]["snapshot"]["contract"].get("side")
    short = _number(points[-1]["snapshot"]["contract"].get("short_strike_usd"))
    long = _number(points[-1]["snapshot"]["contract"].get("long_strike_usd"))
    zones = [_zone(side, price, short, long) for price in prices]
    adverse_steps, favorable_steps = 0, 0
    for before, after in zip(prices, prices[1:]):
        if before is None or after is None or before == after:
            continue
        adverse = after < before if side == "put" else after > before
        adverse_steps += 1 if adverse else 0
        favorable_steps += 0 if adverse else 1
    crossings = sum(1 for before, after in zip(zones, zones[1:]) if before != after)
    if adverse_steps and not favorable_steps:
        classification = "single_direction_adverse_pressure"
    elif favorable_steps and not adverse_steps:
        classification = "single_direction_favorable_relief"
    elif crossings:
        classification = "repeated_cross_zone"
    else:
        classification = "persistent_same_zone"
    return {
        "status": "PATH_OBSERVED",
        "classification": classification,
        "price_sequence": prices,
        "zone_sequence": zones,
        "zone_crossings": crossings,
        "adverse_steps": adverse_steps,
        "favorable_steps": favorable_steps,
        "win_probability": None,
    }


def _responsibility(packet: Mapping[str, Any], path: Mapping[str, Any]) -> dict[str, Any]:
    level = _level(packet)
    expiry = int(level["expiry_ms"])
    cutoff = packet["cutoff"]["decision_asof_ms"]
    observation_start = max(expiry - 1_800_000, 0)
    return {
        "remaining_dte_ms": level["dte_ms"],
        "liability_zone": _zone(
            level["side"],
            _number(level.get("reference_price_usd")),
            _number(level.get("short_strike_usd")),
            _number(level.get("long_strike_usd")),
        ),
        "path_relation": path.get("classification"),
        "settlement_observation": {
            "source": "deribit_official_expiry_observation_rule",
            "identity": "official_expiry_last_30m_observation_window",
            "observation_start_ms": observation_start,
            "observation_end_ms": expiry,
            "ms_until_observation_start": max(observation_start - cutoff, 0),
            "descriptive_only_no_gate": True,
        },
        "execution_allowed": False,
    }


def _source_dependency_diagnostics(packet_sequence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    candidate_ids = set()
    snapshot_ids = set()
    expiry_groups = set()
    side_groups = set()
    read_count = 0
    for packet in packet_sequence:
        snapshot = packet["snapshot"]
        candidate_ids.add(snapshot["candidate_id"])
        snapshot_ids.add(snapshot["snapshot_id"])
        contract = snapshot["contract"]
        expiry_groups.add((
            contract.get("venue"),
            contract.get("underlying"),
            contract.get("settlement_currency"),
            contract.get("expiry_ms"),
        ))
        side_groups.add((contract.get("expiry_ms"), contract.get("side")))
        for record in packets._source_records(snapshot).values():
            identity = record.get("identity")
            source_hash = ((record.get("content") or {}).get("values") or {}).get("source_hash")
            source_hash = source_hash or (record.get("version") or {}).get("payload_hash")
            key = _hash({"identity": identity, "source_hash": source_hash})
            row = groups.setdefault(key, {
                "identity": deepcopy(identity),
                "source_hash": source_hash,
                "read_count": 0,
                "record_ids": [],
                "unique_update_hashes": set(),
            })
            row["read_count"] += 1
            read_count += 1
            row["record_ids"].append(record.get("record_id"))
            row["unique_update_hashes"].add(source_hash or _hash(record.get("content")))
    rows = []
    for key, row in sorted(groups.items()):
        unique = sorted(item for item in row.pop("unique_update_hashes") if item is not None)
        rows.append({
            "group_hash": key,
            **row,
            "unique_update_count": len(unique),
            "unique_update_hashes": unique,
        })
    return {
        "read_count": read_count,
        "unique_update_group_count": len(rows),
        "source_record_groups": rows,
        "dependency_counts": {
            "snapshots": len(snapshot_ids),
            "candidates": len(candidate_ids),
            "expiry_groups": len(expiry_groups),
            "side_groups": len(side_groups),
        },
        "reread_is_not_update": True,
    }


def build_window_evidence(
    packet_sequence: Sequence[Mapping[str, Any]],
    qualification_reports: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str | None = None,
    cutoff_ms: int | None = None,
    native_sources: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Claimed native points require raw source bytes and a fresh A qualification."""
    current, by_snapshot = _current_packet(packet_sequence, candidate_id, cutoff_ms)
    candidate_id = current["snapshot"]["candidate_id"]
    cutoff_ms = current["cutoff"]["decision_asof_ms"]
    future_packets = [
        packet["snapshot"]["snapshot_id"]
        for packet in packet_sequence
        if packet["cutoff"]["decision_asof_ms"] > cutoff_ms
    ]
    if future_packets:
        raise ValueError("window_future_packet_in_sequence")
    reports_by_hash = _bind_reports(qualification_reports, by_snapshot, native_sources)
    native_window = native.qualified_windows(qualification_reports, candidate_id, cutoff_ms)
    _require_hash(native_window, "window_hash")
    point_bindings = []
    selected_packets: list[dict[str, Any]] = []
    best_window = native_window["windows"][0] if native_window.get("windows") else {}
    covered = [row for row in native_window.get("windows") or []
               if row.get("status") in {"NATIVE_WINDOW_QUALIFIED", "SYNTHETIC_WINDOW_COVERED"}]
    if covered:
        best_window = covered[0]
    current_report_in_window = any(
        ref.get("snapshot_id") == current["snapshot"]["snapshot_id"]
        for ref in best_window.get("point_refs") or []
    )
    if best_window.get("status") in {"NATIVE_WINDOW_QUALIFIED", "SYNTHETIC_WINDOW_COVERED"} and not current_report_in_window:
        best_window = dict(deepcopy(best_window))
        best_window["status"] = "INSUFFICIENT_WINDOW"
        best_window["current_qualification_required"] = True
        best_window["reason"] = "current_qualification_report_missing"
    coverage_rows = deepcopy(native_window.get("windows") or [])
    for row in coverage_rows:
        if row.get("requested_window_ms") == best_window.get("requested_window_ms"):
            row.update({key: deepcopy(value) for key, value in best_window.items()})
    for ref in best_window.get("point_refs") or []:
        report = reports_by_hash.get(str(ref["qualification_hash"]))
        packet = by_snapshot.get(str(ref["snapshot_id"]))
        if report is None or packet is None:
            raise ValueError("window_point_binding_missing")
        point_bindings.append({
            "snapshot_id": ref["snapshot_id"],
            "decision_asof_ms": ref["decision_asof_ms"],
            "packet_hash": packet["packet_hash"],
            "qualification_hash": ref["qualification_hash"],
            "qualification_content_hash": _hash(report),
            "evidence_kind": report.get("evidence_kind"),
            "status": report.get("status"),
        })
        selected_packets.append(dict(packet))
    selected_packets.sort(key=lambda packet: packet["cutoff"]["decision_asof_ms"])
    if len(selected_packets) >= 2 and best_window.get("status") in {"NATIVE_WINDOW_QUALIFIED", "SYNTHETIC_WINDOW_COVERED"}:
        delta = _delta(selected_packets[0], selected_packets[-1])
    else:
        delta = {
            "status": "NO_STRICT_DELTA",
            "reason": "qualified_same_candidate_path_missing",
            "strict_same_definition": False,
        }
    path = _path(selected_packets, best_window) if selected_packets else {
        "status": "NO_PATH_DUE_TO_WINDOW_GAP",
        "classification": "gap",
        "reason": "no_window_points",
        "win_probability": None,
    }
    candidate_ids = sorted({packet["snapshot"]["candidate_id"] for packet in packet_sequence})
    body = {
        "schema": WINDOW_EVIDENCE_SCHEMA,
        "candidate_id": candidate_id,
        "snapshot_id": current["snapshot"]["snapshot_id"],
        "packet_hash": current["packet_hash"],
        "cutoff_ms": cutoff_ms,
        "segment_id": native_window.get("segment_id"),
        "native_window_hash": native_window.get("window_hash"),
        "independent_trade_count": native_window.get("independent_trade_count"),
        "coverage": coverage_rows,
        "level": _level(current),
        "delta": delta,
        "path": path,
        "responsibility": _responsibility(current, path),
        "source_breaks": {
            "method_break_observed": native_window.get("method_break_observed") is True,
            "candidate_rotation_observed": len(candidate_ids) > 1,
            "candidate_ids_seen": candidate_ids,
        },
        "source_dependency_diagnostics": _source_dependency_diagnostics(packet_sequence),
        "completeness": {
            "real_window_status": "REAL_WINDOW_BLOCKED"
            if not any(row.get("status") == "NATIVE_WINDOW_QUALIFIED" for row in native_window.get("windows") or [])
            else "REAL_WINDOW_QUALIFIED",
            "path_status": path.get("status"),
            "point_bindings": point_bindings,
            "current_report_in_selected_window": current_report_in_window,
        },
        "evidence_strength": {
            "kind": best_window.get("evidence_kind") or "none",
            "status": best_window.get("status"),
            "describes_data_completeness_only": True,
            "trade_score": None,
            "win_probability": None,
        },
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    return _seal(body, "window_evidence_hash")


def _entry(
    role: str,
    entry_id: str,
    ref: str,
    claim: str,
    *,
    relation: str,
    source_kind: str,
    evidence_group: str,
    evidence_hashes: Sequence[str],
) -> dict[str, Any]:
    body = {
        "id": entry_id,
        "role": role,
        "ref": ref,
        "claim_cn": claim,
        "relation": relation,
        "source_kind": source_kind,
        "evidence_group": evidence_group,
        "evidence_hashes": list(evidence_hashes),
    }
    body["entry_hash"] = _hash(body)
    return body


def _reference_status(reference_analysis: Mapping[str, Any] | None, packet: Mapping[str, Any]) -> dict[str, Any]:
    if reference_analysis is None:
        return {
            "provided": False,
            "terminal_status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
            "status": "REFERENCE_ANALYSIS_MISSING",
            "required_coverage": None,
            "target_status": None,
            "mu_ref_btc": None,
            "M_ref_btc": None,
            "m_ref_btc": None,
            "shadow_inference_allowed": False,
            "reference_analysis_hash": None,
        }
    _require_hash(reference_analysis, "reference_analysis_hash")
    qualification = reference_analysis.get("qualification_report") or {}
    feature_manifest = reference_analysis.get("feature_binding_manifest") or {}
    target_binding = reference_analysis.get("target_price_basis_binding") or {}
    _require_hash(qualification, "qualification_report_hash")
    _require_hash(feature_manifest, "feature_binding_manifest_hash")
    _require_hash(target_binding, "target_price_basis_binding_hash")
    if reference_analysis.get("terminal_status") != "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND":
        raise ValueError("reference_terminal_status_not_admitted_this_round")
    if qualification.get("terminal_status") != "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND":
        raise ValueError("qualification_terminal_status_not_admitted_this_round")
    if feature_manifest.get("terminal_status") not in {
        "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "FEATURES_ONLY_NOT_TARGET_BOUND",
    }:
        raise ValueError("feature_terminal_status_not_admitted_this_round")
    if target_binding.get("terminal_status") != "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND":
        raise ValueError("target_terminal_status_not_admitted_this_round")
    if qualification.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("reference_packet_hash_mismatch")
    if feature_manifest.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("reference_feature_packet_hash_mismatch")
    if target_binding.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("reference_target_packet_hash_mismatch")
    if qualification.get("candidate_id") != packet["snapshot"]["candidate_id"]:
        raise ValueError("reference_candidate_mismatch")
    if feature_manifest.get("candidate_id") != packet["snapshot"]["candidate_id"]:
        raise ValueError("reference_feature_candidate_mismatch")
    if qualification.get("snapshot_id") != packet["snapshot"]["snapshot_id"]:
        raise ValueError("reference_snapshot_mismatch")
    if feature_manifest.get("snapshot_id") != packet["snapshot"]["snapshot_id"]:
        raise ValueError("reference_feature_snapshot_mismatch")
    if feature_manifest.get("catalog_hash") != qualification.get("catalog_hash"):
        raise ValueError("reference_feature_catalog_mismatch")
    if target_binding.get("catalog_hash") != qualification.get("catalog_hash"):
        raise ValueError("reference_target_catalog_mismatch")
    null_keys = ("mu_ref_btc", "M_ref_btc", "m_ref_btc")
    if any(reference_analysis.get(key) is not None for key in null_keys):
        raise ValueError("reference_nonqualified_values_must_stay_null")
    if any(qualification.get(key) is not None for key in null_keys):
        raise ValueError("qualification_nonqualified_values_must_stay_null")
    if any(target_binding.get(key) is not None for key in null_keys):
        raise ValueError("target_nonqualified_values_must_stay_null")
    for node, label in (
        (reference_analysis, "reference"),
        (qualification, "qualification"),
        (feature_manifest, "feature"),
        (target_binding, "target"),
    ):
        if node.get("inference_calls") not in (0, None) or node.get("llm_http_calls") not in (0, None):
            raise ValueError(label + "_calls_must_be_zero")
        if node.get("inference_allowed") is True or node.get("shadow_inference_allowed") is True:
            raise ValueError(label + "_inference_not_admitted")
        if node.get("row_qualified") is True:
            raise ValueError(label + "_row_not_admitted")
    coverage = qualification.get("required_coverage") if isinstance(qualification.get("required_coverage"), Mapping) else {}
    if qualification.get("terminal_status") == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND":
        if coverage.get("missing") in (None, 0) and target_binding.get("status") != "TARGET_BINDING_UNPROVEN":
            raise ValueError("reference_missing_actual_breakpoint")
    return {
        "provided": True,
        "terminal_status": reference_analysis.get("terminal_status"),
        "status": qualification.get("status"),
        "earliest_breakpoint": qualification.get("earliest_breakpoint"),
        "required_coverage": deepcopy(coverage),
        "target_status": target_binding.get("status"),
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
        "shadow_inference_allowed": qualification.get("shadow_inference_allowed") is True,
        "required_missing_features": deepcopy(qualification.get("required_missing_features") or []),
        "reference_analysis_hash": reference_analysis.get("reference_analysis_hash"),
        "qualification_report_hash": qualification.get("qualification_report_hash"),
        "feature_binding_manifest_hash": feature_manifest.get("feature_binding_manifest_hash"),
        "target_price_basis_binding_hash": target_binding.get("target_price_basis_binding_hash"),
        "inference_calls": reference_analysis.get("inference_calls"),
        "llm_http_calls": reference_analysis.get("llm_http_calls"),
    }


def _verify_legacy_review_input(packet: Mapping[str, Any], legacy_review_input: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if legacy_review_input is None:
        return None
    scope = legacy_review_input.get("research_scope") if isinstance(legacy_review_input.get("research_scope"), Mapping) else {}
    rebuilt = legacy_review.build_review_input(packet["snapshot"], research_only=scope.get("research_only") is True)
    if legacy_review_input != rebuilt:
        raise ValueError("legacy_review_input_rebuild_mismatch")
    return {
        "schema": legacy_review_input.get("schema"),
        "response_protocol": legacy_review.RESPONSE_PROTOCOL,
        "projection_hash": legacy_review_input.get("projection_hash"),
        "claim_catalog_hash": legacy_review_input.get("claim_catalog_hash"),
        "packet_hash": legacy_review_input.get("packet_hash"),
        "verified_rebuilt": True,
    }


def _build_catalog(
    packet: Mapping[str, Any],
    window_evidence: Mapping[str, Any],
    risk_status: Mapping[str, Any],
) -> dict[str, Any]:
    wh = window_evidence["window_evidence_hash"]
    ph = packet["packet_hash"]
    claims = [
        _entry("claims", "claim.window.level_contract_position", "window.level",
               "当前合同位置、腿宽、余期、信用和责任区来自冻结程序事实。",
               relation="SUPPORT", source_kind="program_fact", evidence_group="contract_level",
               evidence_hashes=[ph, wh]),
        _entry("claims", "claim.quote.visible_credit", "quote.credit",
               "可见权利金、费用和c-f账来自冻结合同账。",
               relation="SUPPORT", source_kind="program_fact", evidence_group="quote_accounting",
               evidence_hashes=[ph]),
    ]
    if window_evidence["path"].get("status") == "PATH_OBSERVED":
        claims.append(_entry("claims", "claim.window.path_observed", "window.path",
                             "同候选路径已观察到，但它不生成胜率、μ或交易许可。",
                             relation="SUPPORT", source_kind="program_fact", evidence_group="window_path",
                             evidence_hashes=[wh]))
    counterclaims = [
        _entry("counterclaims", "counter.risk.reference_not_qualified", "risk.qualification",
               "旧风险资产本轮不合格，预计赔付仍未量化。",
               relation="COUNTER", source_kind="program_fact", evidence_group="risk_reference",
               evidence_hashes=[risk_status.get("reference_analysis_hash") or ph]),
        _entry("counterclaims", "counter.compensation.expected_payout_unknown", "q2.expected_payout",
               "μ为空时，Q2不能给正面经济倾向。",
               relation="COUNTER", source_kind="program_fact", evidence_group="compensation_accounting",
               evidence_hashes=[ph]),
    ]
    if window_evidence["path"].get("status") != "PATH_OBSERVED":
        counterclaims.append(_entry("counterclaims", "counter.window.path_unavailable", "window.path",
                                    "窗口缺口阻止路径结论。",
                                    relation="COUNTER", source_kind="program_fact", evidence_group="window_path",
                                    evidence_hashes=[wh]))
    if window_evidence["level"]["anchor_gex_wall_group"]["present_inputs"]:
        counterclaims.append(_entry("counterclaims", "counter.source.dependent_anchor_gex_wall",
                                    "source.anchor_gex_wall",
                                    "Anchor、GEX和wall同属依赖证据组，不能计作多条独立确认。",
                                    relation="COUNTER", source_kind="program_fact",
                                    evidence_group="anchor_gex_wall_dependent_group", evidence_hashes=[wh]))
    margin = window_evidence["level"].get("M_ref_btc")
    if _number(margin) is not None and float(margin) <= 0:
        counterclaims.append(_entry("counterclaims", "counter.compensation.nonpositive_margin",
                                    "q2.reference_margin",
                                    "非正M阻止EXPLAINED，并要求重定价复核。",
                                    relation="COUNTER", source_kind="program_fact",
                                    evidence_group="compensation_accounting", evidence_hashes=[ph]))
    gaps = []
    if window_evidence["level"].get("mu_ref_btc") is None:
        gaps.append(_entry("gaps", "gap.compensation.mu_reference_missing", "risk.mu_ref",
                           "μ缺失，预计赔付保持未知。",
                           relation="GAP", source_kind="program_fact", evidence_group="risk_reference",
                           evidence_hashes=[ph, risk_status.get("reference_analysis_hash") or ph]))
    if window_evidence["completeness"]["real_window_status"] != "REAL_WINDOW_QUALIFIED":
        gaps.append(_entry("gaps", "gap.window.real_native_window_missing", "window.real_native",
                           "真实原生同候选窗口仍阻断或不足。",
                           relation="GAP", source_kind="program_fact", evidence_group="window_path",
                           evidence_hashes=[wh]))
    reassess = [
        _entry("reassess_conditions", "reassess.new_quote_or_window", "identity.projection",
               "新报价、新packet或新window hash会使本次shadow选择失效。",
               relation="REASSESS", source_kind="program_fact", evidence_group="identity",
               evidence_hashes=[ph, wh]),
        _entry("reassess_conditions", "reassess.risk_reference_qualification", "risk.qualification",
               "只有严格风险资格改变后，才能重新评估预计赔付参考。",
               relation="REASSESS", source_kind="program_fact", evidence_group="risk_reference",
               evidence_hashes=[risk_status.get("reference_analysis_hash") or ph]),
    ]
    if any(item["id"] == "counter.compensation.nonpositive_margin" for item in counterclaims):
        reassess.append(_entry("reassess_conditions", "reassess.compensation_reprice",
                               "q2.reprice", "任何补偿解释前必须先重定价。",
                               relation="REASSESS", source_kind="program_fact",
                               evidence_group="compensation_accounting", evidence_hashes=[ph]))
    body = {
        "schema": CLAIM_CATALOG_SCHEMA,
        "response_protocol": RESPONSE_PROTOCOL,
        "candidate_id": packet["snapshot"]["candidate_id"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "packet_hash": ph,
        "window_evidence_hash": wh,
        "claims": claims,
        "counterclaims": counterclaims,
        "gaps": gaps,
        "reassess_conditions": reassess,
        "catalog_policy": {
            "unknown_important_counter": "CATALOG_GAP",
            "free_financial_text_allowed": False,
            "execution_allowed": False,
        },
    }
    return _seal(body, "catalog_hash")


def _mandatory(catalog: Mapping[str, Any], window_evidence: Mapping[str, Any], risk_status: Mapping[str, Any]) -> dict[str, list[str]]:
    ids = {role: {item["id"] for item in catalog[role]} for role in (
        "claims", "counterclaims", "gaps", "reassess_conditions")}
    mandatory = {
        "claim_ids": ["claim.window.level_contract_position", "claim.quote.visible_credit"],
        "counter_ids": ["counter.risk.reference_not_qualified", "counter.compensation.expected_payout_unknown"],
        "gap_ids": [],
        "reassess_condition_ids": ["reassess.new_quote_or_window", "reassess.risk_reference_qualification"],
    }
    if window_evidence["level"].get("mu_ref_btc") is None:
        mandatory["gap_ids"].append("gap.compensation.mu_reference_missing")
    if window_evidence["path"].get("status") != "PATH_OBSERVED":
        mandatory["counter_ids"].append("counter.window.path_unavailable")
    if window_evidence["completeness"]["real_window_status"] != "REAL_WINDOW_QUALIFIED":
        mandatory["gap_ids"].append("gap.window.real_native_window_missing")
    if "counter.compensation.nonpositive_margin" in ids["counterclaims"]:
        mandatory["counter_ids"].append("counter.compensation.nonpositive_margin")
        mandatory["reassess_condition_ids"].append("reassess.compensation_reprice")
    role_for_key = {
        "claim_ids": "claims",
        "counter_ids": "counterclaims",
        "gap_ids": "gaps",
        "reassess_condition_ids": "reassess_conditions",
    }
    return {key: [item for item in values if item in ids[role_for_key[key]]]
            for key, values in mandatory.items()}


def build_shadow_projection(
    packet: Mapping[str, Any],
    window_evidence: Mapping[str, Any],
    reference_analysis: Mapping[str, Any] | None = None,
    legacy_review_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packets.replay_packet(packet)
    _require_hash(window_evidence, "window_evidence_hash")
    if window_evidence.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("window_packet_hash_mismatch")
    if window_evidence.get("snapshot_id") != packet["snapshot"]["snapshot_id"]:
        raise ValueError("window_snapshot_mismatch")
    risk_status = _reference_status(reference_analysis, packet)
    legacy = _verify_legacy_review_input(packet, legacy_review_input)
    catalog = _build_catalog(packet, window_evidence, risk_status)
    mandatory = _mandatory(catalog, window_evidence, risk_status)
    economics = packet["snapshot"]["economics"]
    fee = packet["snapshot"]["fee"]
    loss_budget = economics.get("loss_budget_btc")
    if loss_budget is None and economics.get("visible_credit_btc") is not None and fee.get("amount_btc") is not None:
        loss_budget = economics["visible_credit_btc"] - fee["amount_btc"]
    synthetic_account = _synthetic_account_fixture(packet)
    body = {
        "schema": PROJECTION_SCHEMA,
        "response_protocol": RESPONSE_PROTOCOL,
        "candidate_id": packet["snapshot"]["candidate_id"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "packet_hash": packet["packet_hash"],
        "window_evidence_hash": window_evidence["window_evidence_hash"],
        "claim_catalog_hash": catalog["catalog_hash"],
        "source_bindings": {
            "packet_hash": packet["packet_hash"],
            "window_evidence_hash": window_evidence["window_evidence_hash"],
            "native_window_hash": window_evidence.get("native_window_hash"),
            "reference_analysis_hash": risk_status.get("reference_analysis_hash"),
            "legacy_review_input": legacy,
        },
        "risk_qualification": risk_status,
        "deterministic_account": {
            "visible_credit_btc": economics.get("visible_credit_btc"),
            "fee_btc": fee.get("amount_btc"),
            "c_minus_f_btc": loss_budget,
            "mu_ref_btc": economics.get("mu_ref_btc"),
            "M_ref_btc": economics.get("reference_margin_btc"),
            "scenario_liability": deepcopy(packet["snapshot"]["liability"].get("payout_table") or []),
            "account_fixture_kind": "SYNTHETIC_ACCOUNT_FIXTURE" if synthetic_account else None,
        },
        "q1_material": {
            "level": deepcopy(window_evidence["level"]),
            "delta": deepcopy(window_evidence["delta"]),
            "path": deepcopy(window_evidence["path"]),
            "responsibility": deepcopy(window_evidence["responsibility"]),
            "hard_constraints": ["execution_allowed_false", "review_gate_unchanged", "window_does_not_create_mu_or_permission"],
            "observational_soft_flaws": [
                item for item in [
                    "path_unavailable" if window_evidence["path"].get("status") != "PATH_OBSERVED" else None,
                    "real_window_blocked" if window_evidence["completeness"]["real_window_status"] != "REAL_WINDOW_QUALIFIED" else None,
                    "risk_reference_not_qualified" if risk_status["terminal_status"] != "QUALIFIED_SHADOW_REFERENCE_ONLY" else None,
                ] if item
            ],
            "old_review_invalidated_by": ["packet_hash", "window_evidence_hash", "projection_hash"],
        },
        "q2_material": {
            "mode": "EXPECTED_PAYOUT_UNKNOWN",
            "positive_economic_bias_allowed": False,
            "show_only_c_f_and_scenarios": True,
            "production_financial_value_write_allowed": False,
            "synthetic_account_fixture": synthetic_account,
        },
        "claim_catalog": catalog,
        "selection_validator": {
            "wire_keys": sorted(WIRE_RESPONSE_KEYS),
            "max_selected_ids_per_role": MAX_SELECTED_IDS,
            "mandatory": mandatory,
            "missing_mu_prevents_explained": window_evidence["level"].get("mu_ref_btc") is None,
            "nonpositive_M_prevents_explained": _number(window_evidence["level"].get("M_ref_btc")) is not None
            and float(window_evidence["level"]["M_ref_btc"]) <= 0,
            "unknown_important_counter": "CATALOG_GAP",
            "no_gate_change": True,
        },
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    return _seal(body, "projection_hash")


def _catalog_by_role(catalog: Mapping[str, Any]) -> dict[str, dict[str, Mapping[str, Any]]]:
    return {
        role: {item["id"]: item for item in _as_list(catalog.get(role))}
        for role in ("claims", "counterclaims", "gaps", "reassess_conditions")
    }


def _selected_ids(payload: Mapping[str, Any], key: str, role: str, by_role: Mapping[str, Mapping[str, Any]]) -> list[str]:
    values = payload.get(key)
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ShadowValidationError("selected ids must be string lists")
    if len(values) > MAX_SELECTED_IDS:
        raise ShadowValidationError("selected ids exceed bounded limit")
    known_any = set().union(*(set(items) for items in by_role.values()))
    out = []
    for item in values:
        if item not in by_role[role]:
            if item in known_any:
                raise ShadowValidationError("selected id has wrong role")
            raise ShadowValidationError("CATALOG_GAP:unknown_selected_id:" + item)
        out.append(item)
    return out


def _require_all(required: Sequence[str], selected: Sequence[str], message: str) -> None:
    missing = [item for item in required if item not in selected]
    if missing:
        raise ShadowValidationError(message + ":" + ",".join(missing))


def validate_shadow_selection(
    packet: Mapping[str, Any],
    payload: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    packets.replay_packet(packet)
    _require_hash(projection, "projection_hash")
    if projection.get("packet_hash") != packet.get("packet_hash"):
        raise ShadowValidationError("selection_packet_hash_mismatch")
    if not isinstance(payload, Mapping) or set(payload) != WIRE_RESPONSE_KEYS:
        raise ShadowValidationError("selection fields do not match the frozen schema")
    if payload["response_protocol"] != RESPONSE_PROTOCOL:
        raise ShadowValidationError("selection response protocol mismatch")
    if payload["reviewed_snapshot_id"] != packet["snapshot"]["snapshot_id"]:
        raise ShadowValidationError("selection read a different snapshot")
    if payload["projection_hash"] != projection["projection_hash"]:
        raise ShadowValidationError("selection projection hash does not match")
    if payload["claim_catalog_hash"] != projection["claim_catalog_hash"]:
        raise ShadowValidationError("selection catalog hash does not match")
    if payload["applicability"] not in {"EXPLAINED", "CONTESTED", "UNABLE_TO_JUDGE"}:
        raise ShadowValidationError("invalid applicability")
    catalog = projection["claim_catalog"]
    _require_hash(catalog, "catalog_hash")
    by_role = _catalog_by_role(catalog)
    selected_claims = _selected_ids(payload, "selected_claim_ids", "claims", by_role)
    selected_counters = _selected_ids(payload, "selected_counter_ids", "counterclaims", by_role)
    selected_gaps = _selected_ids(payload, "selected_gap_ids", "gaps", by_role)
    selected_reassess = _selected_ids(payload, "selected_reassess_condition_ids", "reassess_conditions", by_role)
    all_ids = selected_claims + selected_counters + selected_gaps + selected_reassess
    if len(all_ids) != len(set(all_ids)):
        raise ShadowValidationError("selected ids contain duplicates")
    if not selected_counters:
        raise ShadowValidationError("shadow selection requires at least one counterclaim")
    if not selected_reassess:
        raise ShadowValidationError("shadow selection requires at least one reassess condition")
    validator = projection["selection_validator"]
    mandatory = validator.get("mandatory") if isinstance(validator.get("mandatory"), Mapping) else {}
    _require_all(_as_list(mandatory.get("claim_ids")), selected_claims, "mandatory claim missing")
    _require_all(_as_list(mandatory.get("counter_ids")), selected_counters, "mandatory counter missing")
    _require_all(_as_list(mandatory.get("gap_ids")), selected_gaps, "mandatory gap missing")
    _require_all(_as_list(mandatory.get("reassess_condition_ids")), selected_reassess,
                 "mandatory reassess missing")
    if validator.get("missing_mu_prevents_explained") and payload["applicability"] == "EXPLAINED":
        raise ShadowValidationError("missing mu reference prevents EXPLAINED")
    if validator.get("nonpositive_M_prevents_explained") and payload["applicability"] == "EXPLAINED":
        raise ShadowValidationError("nonpositive M prevents EXPLAINED")
    body = {
        "schema": SELECTION_SCHEMA,
        "response_protocol": RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": payload["reviewed_snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": payload["applicability"],
        "selected_claim_ids": selected_claims,
        "selected_counter_ids": selected_counters,
        "selected_gap_ids": selected_gaps,
        "selected_reassess_condition_ids": selected_reassess,
        "selected_items": {
            "claims": [deepcopy(by_role["claims"][item]) for item in selected_claims],
            "counterclaims": [deepcopy(by_role["counterclaims"][item]) for item in selected_counters],
            "gaps": [deepcopy(by_role["gaps"][item]) for item in selected_gaps],
            "reassess_conditions": [deepcopy(by_role["reassess_conditions"][item]) for item in selected_reassess],
        },
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    return _seal(body, "selection_hash")


def required_selection_payload(projection: Mapping[str, Any], applicability: str | None = None) -> dict[str, Any]:
    _require_hash(projection, "projection_hash")
    mandatory = projection["selection_validator"]["mandatory"]
    if applicability is None:
        applicability = (
            "UNABLE_TO_JUDGE"
            if projection["selection_validator"].get("missing_mu_prevents_explained")
            else "CONTESTED"
        )
    return {
        "response_protocol": RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": projection["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": applicability,
        "selected_claim_ids": list(mandatory.get("claim_ids") or []),
        "selected_counter_ids": list(mandatory.get("counter_ids") or []),
        "selected_gap_ids": list(mandatory.get("gap_ids") or []),
        "selected_reassess_condition_ids": list(mandatory.get("reassess_condition_ids") or []),
    }


def project_q1_q2(
    packet: Mapping[str, Any],
    projection: Mapping[str, Any],
    validated_selection: Mapping[str, Any],
) -> dict[str, Any]:
    packets.replay_packet(packet)
    _require_hash(projection, "projection_hash")
    _require_hash(validated_selection, "selection_hash")
    if validated_selection.get("projection_hash") != projection.get("projection_hash"):
        raise ShadowValidationError("selection_projection_hash_mismatch")
    account = projection["deterministic_account"]
    q1_material = projection["q1_material"]
    path = q1_material["path"]
    delta = q1_material["delta"]
    credit_delta = None
    margin_delta = None
    if isinstance(delta.get("changes"), Mapping):
        credit_delta = delta["changes"].get("visible_credit_btc_delta")
        margin_delta = delta["changes"].get("M_ref_btc_delta")
    q1_improves = path.get("classification") == "single_direction_favorable_relief"
    q2_worsens = (
        delta.get("synthetic_account_fixture_same_basis") is True
        and _number(margin_delta) is not None
        and float(margin_delta) < 0
    )
    body = {
        "schema": Q1_Q2_SCHEMA,
        "response_protocol": RESPONSE_PROTOCOL,
        "candidate_id": projection["candidate_id"],
        "snapshot_id": projection["snapshot_id"],
        "packet_hash": packet["packet_hash"],
        "projection_hash": projection["projection_hash"],
        "selection_hash": validated_selection["selection_hash"],
        "q1": {
            "current_position": deepcopy(q1_material["level"]),
            "level_delta_path": {
                "level": deepcopy(q1_material["level"]),
                "delta": deepcopy(delta),
                "path": deepcopy(path),
            },
            "hard_constraints": deepcopy(q1_material["hard_constraints"]),
            "observational_soft_flaws": deepcopy(q1_material["observational_soft_flaws"]),
            "risk_reference_consumed": False,
            "unquantified_observations": [
                "window evidence is applicability evidence only",
                "same anchor with a different path does not create win probability",
            ],
            "reassess_when": deepcopy(q1_material["old_review_invalidated_by"]),
        },
        "q2": {
            "mode": projection["q2_material"]["mode"],
            "visible_credit_btc": account.get("visible_credit_btc"),
            "fee_btc": account.get("fee_btc"),
            "c_minus_f_btc": account.get("c_minus_f_btc"),
            "mu_ref_btc": account.get("mu_ref_btc"),
            "M_ref_btc": account.get("M_ref_btc"),
            "expected_payout_status": "UNKNOWN",
            "positive_economic_bias_allowed": False,
            "economic_comparison_status": (
                "SYNTHETIC_ACCOUNT_FIXTURE"
                if delta.get("synthetic_account_fixture_same_basis") is True
                else "UNKNOWN"
            ),
            "visible_credit_delta_btc": credit_delta,
            "M_ref_delta_btc": margin_delta,
            "scenario_responsibility": deepcopy(account.get("scenario_liability") or []),
            "q1_can_improve_while_q2_worsens": bool(q1_improves and q2_worsens),
        },
        "forbidden_claims": [
            "win_probability",
            "positive_economic_bias",
            "production_risk_provider_admitted",
            "execution_permission",
        ],
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    return _seal(body, "q1_q2_hash")
