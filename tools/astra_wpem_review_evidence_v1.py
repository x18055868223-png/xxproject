"""Product Review Evidence Package composer for a single Astra candidate.

This is a product-facing wrapper around already-audited packet/window/projection
artifacts. It does not rename the shadow evidence into a release protocol: the
shadow artifacts remain nested source evidence and this package declares only a
candidate review-evidence role.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from . import astra_wpem_native_v1 as native
    from . import astra_wpem_shadow_v1 as shadow
else:
    import astra_candidate_asof_v1 as packets
    import astra_wpem_native_v1 as native
    import astra_wpem_shadow_v1 as shadow

SCHEMA = "wpem_review_evidence@1.1.0"
COMPAT_SCHEMAS = {"wpem_review_evidence@1.0.1"}
LEGACY_SCHEMAS = {"wpem_review_evidence@1.0.0"}
VALIDATION_SCHEMA = "wpem_review_evidence_validation@1.0.0"
ROLE = "candidate"
PACKAGE_IDENTITY = "PRODUCT_REVIEW_EVIDENCE_PACKAGE"
CURRENT_CROSS_SECTION_SCHEMA = "wpem_review_current_cross_section@1.0.0"
SAME_CANDIDATE_WINDOW_SCHEMA = "wpem_review_same_candidate_window@1.0.1"
LEGACY_SAME_CANDIDATE_WINDOW_SCHEMAS = {"wpem_review_same_candidate_window@1.0.0"}
COVERAGE_SUMMARY_SCHEMA = "wpem_window_coverage_summary@1.0.0"
COVERAGE_SUMMARY_FIELDS = (
    "requested_window_ms",
    "actual_coverage_ms",
    "coverage_start_ms",
    "coverage_end_ms",
    "cutoff_ms",
    "point_count",
    "qualified_points",
    "native_qualified_points",
    "max_gap_ms",
    "status",
    "evidence_kind",
)


def _hash(value: Any) -> str:
    return packets.core._hash(value)


def _seal(body: Mapping[str, Any], key: str) -> dict[str, Any]:
    data = dict(deepcopy(body))
    data[key] = _hash(data)
    return data


def _seal_review_evidence(body: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(deepcopy(body))
    digest = _hash(data)
    data["evidence_hash"] = digest
    data["review_evidence_hash"] = digest
    return data


def _require_review_evidence_hash(evidence: Mapping[str, Any]) -> None:
    body = {k: v for k, v in evidence.items() if k not in {"evidence_hash", "review_evidence_hash"}}
    digest = _hash(body)
    if evidence.get("evidence_hash") != digest:
        raise ValueError("evidence_hash_mismatch")
    if evidence.get("review_evidence_hash") not in {None, digest}:
        raise ValueError("review_evidence_hash_mismatch")


def _require_hash(value: Mapping[str, Any], key: str) -> None:
    native.require_hash(value, key)


def _encode_native_sources(native_sources: Mapping[str, bytes]) -> dict[str, str]:
    encoded: dict[str, str] = {}
    for key, value in sorted((native_sources or {}).items()):
        if not isinstance(key, str):
            raise ValueError("native_source_hash_key_must_be_string")
        if not isinstance(value, (bytes, bytearray)):
            raise ValueError("native_source_bytes_required")
        encoded[key] = base64.b64encode(bytes(value)).decode("ascii")
    return encoded


def _decode_native_sources(encoded: Mapping[str, Any]) -> dict[str, bytes]:
    if encoded is None:
        return {}
    if not isinstance(encoded, Mapping):
        raise ValueError("native_sources_b64_must_be_mapping")
    decoded: dict[str, bytes] = {}
    for key, value in encoded.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("native_sources_b64_malformed")
        try:
            decoded[key] = base64.b64decode(value.encode("ascii"), validate=True)
        except Exception as exc:  # noqa: BLE001 - convert codec errors into audit error.
            raise ValueError("native_sources_b64_malformed") from exc
    return decoded


def _safe_copy(value: Any, label: str) -> Any:
    data = deepcopy(value)
    try:
        _hash(data)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + "_not_json_safe") from exc
    return data


def _as_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [_safe_copy(value, label)]
    if isinstance(value, (str, bytes, bytearray)):
        raise ValueError(label + "_must_be_sequence")
    rows = list(value)
    return [_safe_copy(row, label) for row in rows]


def _composition_inputs(
    packet_sequence: Sequence[Mapping[str, Any]],
    qualification_reports: Sequence[Mapping[str, Any]],
    cross_section: Sequence[Mapping[str, Any]],
    risk_reference: Mapping[str, Any] | None,
    release_identity: Mapping[str, Any] | None,
    native_sources: Mapping[str, bytes],
) -> dict[str, Any]:
    body = {
        "packet_sequence": _as_list(packet_sequence, "composition_packet_sequence"),
        "qualification_reports": _as_list(qualification_reports, "composition_qualification_reports"),
        "cross_section": _as_list(cross_section, "composition_cross_section"),
        "risk_reference": None if risk_reference is None else _safe_copy(risk_reference, "composition_risk_reference"),
        "release_identity": None if release_identity is None else _safe_copy(release_identity, "composition_release_identity"),
        "native_sources_b64": _encode_native_sources(native_sources),
    }
    return _seal(body, "composition_inputs_hash")


def _packet_rows_for_snapshot(
    snapshot: Mapping[str, Any],
    packet_sequence: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    snapshot_copy = _safe_copy(snapshot, "snapshot")
    if not isinstance(snapshot_copy, Mapping):
        raise ValueError("snapshot_must_be_mapping")
    snapshot_id = snapshot_copy.get("snapshot_id")
    candidate_id = snapshot_copy.get("candidate_id")
    if not isinstance(snapshot_id, str) or not isinstance(candidate_id, str):
        raise ValueError("snapshot_identity_missing")

    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for packet in packet_sequence:
        packets.replay_packet(packet)
        row = dict(deepcopy(packet))
        rows.append(row)
        row_snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), Mapping) else {}
        if row_snapshot.get("snapshot_id") == snapshot_id:
            if row_snapshot.get("candidate_id") != candidate_id:
                raise ValueError("current_packet_candidate_mismatch")
            if row_snapshot != snapshot_copy:
                raise ValueError("current_packet_snapshot_content_mismatch")
            current = row

    built_current = False
    if current is None:
        current = packets.build_packet(snapshot_copy)
        rows.append(current)
        built_current = True
    rows.sort(key=lambda packet: packet["cutoff"]["decision_asof_ms"])
    return rows, current, built_current


def _release_identity(release_identity: Mapping[str, Any] | None) -> dict[str, Any]:
    if release_identity is None:
        return {
            "status": "NOT_PROVIDED_NOT_BLOCKING",
            "provided": False,
            "release_identity_hash": None,
            "release_authority": False,
            "loaded_source_hash_or_account_inventory_required": False,
        }
    value = _safe_copy(release_identity, "release_identity")
    return {
        "status": "PROVIDED_FOR_IDENTITY_ONLY",
        "provided": True,
        "value": value,
        "release_identity_hash": _hash(value),
        "release_authority": False,
        "loaded_source_hash_or_account_inventory_required": False,
    }


def _source_clocks(snapshot: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    fact = snapshot.get("fact_context") if isinstance(snapshot.get("fact_context"), Mapping) else {}
    return {
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "market_valuation_ts_ms": market.get("valuation_ts_ms"),
        "market_received_ts_ms": market.get("received_ts_ms"),
        "fact_snapshot_ts_ms": fact.get("snapshot_ts_ms"),
        "price_observed_ms": fact.get("price_observed_ms"),
        "source_manifest_hashes": deepcopy((packet.get("versions") or {}).get("source_manifest_hashes") or []),
        "source_records": deepcopy((packet.get("sources") or {}).get("record_ids") or []),
    }


def _snapshot_cross_section_payload(packet: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = packet["snapshot"]
    contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    economics = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
    fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
    liability = snapshot.get("liability") if isinstance(snapshot.get("liability"), Mapping) else {}
    return {
        "contract": {
            "side": contract.get("side"),
            "quantity_btc": contract.get("quantity_btc"),
            "short_strike_usd": contract.get("short_strike_usd"),
            "long_strike_usd": contract.get("long_strike_usd"),
            "width_usd": contract.get("width_usd"),
            "expiry_ms": contract.get("expiry_ms"),
        },
        "quote_fee_account": {
            "reference_price_usd": market.get("reference_price_usd"),
            "reference_source": market.get("reference_source"),
            "visible_credit_btc": economics.get("visible_credit_btc"),
            "fee_btc": fee.get("amount_btc"),
            "loss_budget_btc": economics.get("loss_budget_btc"),
            "quote_fee_deterministic_only": True,
        },
        "liability": {
            "signed_distance_to_short_pct": liability.get("signed_distance_to_short_pct"),
            "signed_distance_to_long_pct": liability.get("signed_distance_to_long_pct"),
            "breakeven_usd": deepcopy(liability.get("breakeven_usd")),
            "payout_table": deepcopy(liability.get("payout_table") or []),
        },
        "status": {
            "snapshot_status": snapshot.get("status"),
            "gap_reasons": deepcopy(snapshot.get("gap_reasons") or []),
            "execution_allowed": snapshot.get("execution_allowed"),
        },
        "source_clock": _source_clocks(snapshot, packet),
    }


def _peer_snapshot_from_cross_section_row(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    snapshot = row.get("snapshot") if isinstance(row, Mapping) else None
    if isinstance(snapshot, Mapping):
        return snapshot
    if row.get("schema") == "astra_underwriting_snapshot@2.0.0" and "snapshot_id" in row:
        return row
    return None


def _current_cross_section(
    packet: Mapping[str, Any],
    cross_section: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    snapshot = packet["snapshot"]
    raw_rows = _as_list(cross_section, "cross_section")
    expected = {
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "packet_hash": packet["packet_hash"],
    }
    rows = []
    for index, row in enumerate(raw_rows):
        if not isinstance(row, Mapping):
            raise ValueError("cross_section_row_must_be_mapping")
        peer_snapshot = _peer_snapshot_from_cross_section_row(row)
        if peer_snapshot is not None:
            claimed = row if row.get("snapshot") is not peer_snapshot else {k: v for k, v in row.items() if k != "snapshot"}
            peer_packet = packets.build_packet(peer_snapshot)
            peer = peer_packet["snapshot"]
            if row.get("snapshot") is not None:
                for key in ("candidate_id", "snapshot_id"):
                    if claimed.get(key) not in {None, peer.get(key)}:
                        raise ValueError("cross_section_peer_claim_mismatch:" + key)
                if claimed.get("decision_asof_ms") not in {None, peer_packet["cutoff"]["decision_asof_ms"]}:
                    raise ValueError("cross_section_peer_claim_mismatch:decision_asof_ms")
                if claimed.get("packet_hash") not in {None, peer_packet["packet_hash"]}:
                    raise ValueError("cross_section_peer_claim_mismatch:packet_hash")
            if peer_packet["cutoff"]["decision_asof_ms"] > expected["decision_asof_ms"]:
                raise ValueError("cross_section_peer_future_asof")
            comparable = peer_packet["cutoff"]["decision_asof_ms"] == expected["decision_asof_ms"]
            payload = _snapshot_cross_section_payload(peer_packet)
            rows.append({
                "row_index": index,
                "row_type": "peer_snapshot",
                "role": "PEER",
                "candidate_id": peer["candidate_id"],
                "snapshot_id": peer["snapshot_id"],
                "decision_asof_ms": peer_packet["cutoff"]["decision_asof_ms"],
                "packet_hash": peer_packet["packet_hash"],
                "comparability": "COMPARABLE_PEER" if comparable else "NON_COMPARABLE_SNAPSHOT_ASOF",
                "same_decision_asof": comparable,
                "not_used_for_window": True,
                "not_ranked_or_scored": True,
                "raw_row_hash": _hash(row),
                **payload,
            })
            continue
        for key in ("candidate_id", "snapshot_id", "decision_asof_ms"):
            if row.get(key) != expected[key]:
                raise ValueError("cross_section_identity_mismatch:" + key)
        if row.get("packet_hash") not in {None, expected["packet_hash"]}:
            raise ValueError("cross_section_identity_mismatch:packet_hash")
        rows.append({
            "row_index": index,
            "row_type": "current_identity",
            "role": "CURRENT",
            "candidate_id": expected["candidate_id"],
            "snapshot_id": expected["snapshot_id"],
            "decision_asof_ms": expected["decision_asof_ms"],
            "packet_hash": row.get("packet_hash") or expected["packet_hash"],
            "comparability": "CURRENT",
            "same_decision_asof": True,
            "row_role": row.get("row_role"),
            "source": row.get("source"),
            "raw_row_hash": _hash(row),
        })
    current_payload = _snapshot_cross_section_payload(packet)
    return {
        "schema": CURRENT_CROSS_SECTION_SCHEMA,
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "packet_hash": packet["packet_hash"],
        "source_packet_schema": packet.get("schema"),
        "source_kind": (packet.get("provenance") or {}).get("source_kind"),
        "asof_join_rule": (
            "current_cross_section binds current and peer snapshots by their own frozen packet identity; "
            "COMPARABLE peer rows require the same decision_asof_ms, while NON_COMPARABLE rows remain audit-only. "
            "Peer rows cannot fill same_candidate_window path points, create rankings, calculate EV, or change gates."
        ),
        **current_payload,
        "source_records": deepcopy((packet.get("sources") or {}).get("record_ids") or []),
        "source_manifest_hashes": deepcopy((packet.get("versions") or {}).get("source_manifest_hashes") or []),
        "cross_section_rows": rows,
        "cross_section_hash": _hash(rows),
        "raw_cross_section_hash": _hash(raw_rows),
    }


def _same_candidate_window(window_evidence: Mapping[str, Any]) -> dict[str, Any]:
    path = window_evidence.get("path") if isinstance(window_evidence.get("path"), Mapping) else {}
    completeness = window_evidence.get("completeness") if isinstance(window_evidence.get("completeness"), Mapping) else {}
    strength = window_evidence.get("evidence_strength") if isinstance(window_evidence.get("evidence_strength"), Mapping) else {}
    source_breaks = deepcopy(window_evidence.get("source_breaks") or {})
    observed = path.get("status") == "PATH_OBSERVED"
    status = "OBSERVED" if observed else "INSUFFICIENT"
    return {
        "schema": SAME_CANDIDATE_WINDOW_SCHEMA,
        "status": status,
        "source_schema": window_evidence.get("schema"),
        "source_window_evidence_hash": window_evidence.get("window_evidence_hash"),
        "source_role": "SHADOW_WINDOW_INPUT_NOT_PRODUCT_PROTOCOL",
        "qualification": {
            "status": strength.get("status"),
            "kind": strength.get("kind"),
            "real_window_status": completeness.get("real_window_status"),
            "current_report_in_selected_window": completeness.get("current_report_in_selected_window"),
            "native_window_hash": window_evidence.get("native_window_hash"),
            "describes_data_completeness_only": True,
        },
        "LevelDeltaPath": {
            "level": deepcopy(window_evidence.get("level")),
            "delta": deepcopy(window_evidence.get("delta")),
            "path": deepcopy(window_evidence.get("path")),
            "responsibility": deepcopy(window_evidence.get("responsibility")),
        },
        "source_method_breaks": {
            **source_breaks,
            "market_trend_claim_allowed": False,
            "candidate_rotation_or_method_break_is_not_market_trend": True,
        },
        "coverage_summary": _coverage_summary(window_evidence),
        "dependency_diagnostics": deepcopy(window_evidence.get("source_dependency_diagnostics")),
        "insufficient_reason": None if observed else path.get("reason") or strength.get("status"),
    }


def _coverage_summary(window_evidence: Mapping[str, Any]) -> dict[str, Any]:
    coverage = window_evidence.get("coverage")
    if coverage is None:
        rows = []
    elif not isinstance(coverage, Sequence) or isinstance(coverage, (str, bytes, bytearray)):
        raise ValueError("window_coverage_must_be_sequence")
    else:
        rows = []
        for row in coverage:
            if not isinstance(row, Mapping):
                raise ValueError("window_coverage_row_must_be_mapping")
            rows.append({key: deepcopy(row.get(key)) for key in COVERAGE_SUMMARY_FIELDS})
    return {
        "schema": COVERAGE_SUMMARY_SCHEMA,
        "source_field": "window_evidence.coverage",
        "copied_fields": list(COVERAGE_SUMMARY_FIELDS),
        "windows": rows,
        "policy": {
            "raw_point_refs_excluded": True,
            "raw_source_bytes_excluded": True,
            "private_source_blobs_excluded": True,
            "does_not_change_window_qualification": True,
            "does_not_grant_execution": True,
            "does_not_change_financial_or_review_gates": True,
        },
    }


def _legacy_same_candidate_window_expected(
    actual: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
) -> dict[str, Any]:
    expected = deepcopy(dict(rebuilt))
    if actual.get("schema") in LEGACY_SAME_CANDIDATE_WINDOW_SCHEMAS and "coverage_summary" not in actual:
        expected["schema"] = actual.get("schema")
        expected.pop("coverage_summary", None)
    return expected


def _require_schema_shape(evidence: Mapping[str, Any]) -> None:
    if evidence.get("schema") == SCHEMA:
        if not isinstance(evidence.get("evidence_routing"), Mapping):
            raise ValueError("review_evidence_routing_missing")
    elif "evidence_routing" in evidence:
        raise ValueError("legacy_review_evidence_routing_downgrade")
    same_window = evidence.get("same_candidate_window")
    if not isinstance(same_window, Mapping):
        raise ValueError("same_candidate_window_missing")
    if evidence.get("schema") in LEGACY_SCHEMAS:
        if (same_window.get("schema") not in LEGACY_SAME_CANDIDATE_WINDOW_SCHEMAS
                or "coverage_summary" in same_window):
            raise ValueError("legacy_review_evidence_shape_mismatch")
        return
    if same_window.get("schema") != SAME_CANDIDATE_WINDOW_SCHEMA:
        raise ValueError("review_evidence_window_schema_mismatch")
    if not isinstance(same_window.get("coverage_summary"), Mapping):
        raise ValueError("review_evidence_coverage_summary_missing")


def _risk_status(projection: Mapping[str, Any], risk_reference_provided: bool) -> dict[str, Any]:
    risk = projection.get("risk_qualification") if isinstance(projection.get("risk_qualification"), Mapping) else {}
    account = projection.get("deterministic_account") if isinstance(projection.get("deterministic_account"), Mapping) else {}
    if not risk_reference_provided:
        reason = "REFERENCE_ANALYSIS_MISSING"
    elif risk.get("terminal_status") != "QUALIFIED_SHADOW_REFERENCE_ONLY":
        reason = "RISK_REFERENCE_NOT_QUALIFIED"
    elif account.get("mu_ref_btc") is None:
        reason = "MU_REFERENCE_MISSING"
    elif account.get("M_ref_btc") is None:
        reason = "M_REFERENCE_MISSING"
    else:
        reason = "PRODUCT_REVIEW_EVIDENCE_DOES_NOT_WRITE_PRODUCTION_MU_M"
    return {
        "terminal_status": risk.get("terminal_status"),
        "risk_reference_provided": risk_reference_provided,
        "reference_analysis_hash": risk.get("reference_analysis_hash"),
        "production_mu_ref_btc": None,
        "production_M_ref_btc": None,
        "mu_M_null_reason": reason,
        "source_shadow_mu_ref_btc": account.get("mu_ref_btc"),
        "source_shadow_M_ref_btc": account.get("M_ref_btc"),
        "positive_economic_bias_allowed": False,
        "production_financial_value_write_allowed": False,
    }


def _counter_catalog_gap_reassess(projection: Mapping[str, Any]) -> dict[str, Any]:
    catalog = projection.get("claim_catalog") if isinstance(projection.get("claim_catalog"), Mapping) else {}
    return {
        "source_schema": catalog.get("schema"),
        "source_catalog_hash": catalog.get("catalog_hash"),
        "source_response_protocol": catalog.get("response_protocol"),
        "claims": deepcopy(catalog.get("claims") or []),
        "counterclaims": deepcopy(catalog.get("counterclaims") or []),
        "gaps": deepcopy(catalog.get("gaps") or []),
        "reassess_conditions": deepcopy(catalog.get("reassess_conditions") or []),
        "catalog_policy": deepcopy(catalog.get("catalog_policy") or {}),
        "selection_validator": deepcopy(projection.get("selection_validator") or {}),
        "catalog_gap_is_required_for_unknown_important_counter": True,
    }


def _validity() -> dict[str, Any]:
    return {
        "status": "SELF_HASHED_REPLAYABLE",
        "execution_allowed": False,
        "review_gate_changed": False,
        "financial_gate_changed": False,
        "recomputes_financial_numbers": False,
        "model_inference_calls": 0,
        "llm_http_calls": 0,
        "shadow_window_is_nested_source_evidence_only": True,
    }


def build_review_evidence(
    snapshot: Mapping[str, Any],
    packet_sequence: Sequence[Mapping[str, Any]] = (),
    qualification_reports: Sequence[Mapping[str, Any]] = (),
    cross_section: Sequence[Mapping[str, Any]] = (),
    risk_reference: Mapping[str, Any] | None = None,
    release_identity: Mapping[str, Any] | None = None,
    native_sources: Mapping[str, bytes] | None = None,
    *,
    include_routing: bool = True,
) -> dict[str, Any]:
    """Build a minimal formal review-evidence package for one candidate.

    Native qualification reports are delegated to ``astra_wpem_shadow_v1`` for
    binding and, for real native reports, source-byte requalification. If no
    packet sequence is supplied, this function builds only a current packet from
    ``snapshot`` and ignores standalone qualification reports so a report cannot
    promote itself without its packet sequence.
    """
    packet_rows_input = list(packet_sequence or [])
    packet_rows, current_packet, built_current = _packet_rows_for_snapshot(snapshot, packet_rows_input)
    reports_input = list(qualification_reports or [])
    reports_for_window = reports_input if packet_rows_input else []
    native_source_map = dict(native_sources or {})
    composition = _composition_inputs(
        packet_rows_input, reports_input, cross_section, risk_reference, release_identity, native_source_map
    )

    window = shadow.build_window_evidence(
        packet_rows,
        reports_for_window,
        candidate_id=current_packet["snapshot"]["candidate_id"],
        cutoff_ms=current_packet["cutoff"]["decision_asof_ms"],
        native_sources=native_source_map,
    )
    projection = shadow.build_shadow_projection(current_packet, window, reference_analysis=risk_reference)

    account = deepcopy(projection.get("deterministic_account") or {})
    risk_summary = _risk_status(projection, risk_reference is not None)
    body = {
        "schema": SCHEMA if include_routing else "wpem_review_evidence@1.0.1",
        "role": ROLE,
        "package_identity": {
            "kind": PACKAGE_IDENTITY,
            "product_role": ROLE,
            "candidate_protocol_hint": "bounded_claim_ids@2.5.0-candidate",
            "source_shadow_response_protocol": shadow.RESPONSE_PROTOCOL,
            "shadow_inputs_as_evidence_only": True,
            "not_a_release_or_execution_authority": True,
        },
        "candidate_id": current_packet["snapshot"]["candidate_id"],
        "snapshot_id": current_packet["snapshot"]["snapshot_id"],
        "decision_asof_ms": current_packet["cutoff"]["decision_asof_ms"],
        "packet_hash": current_packet["packet_hash"],
        "current_cross_section": _current_cross_section(current_packet, cross_section),
        "same_candidate_window": _same_candidate_window(window),
        "quote_fee_deterministic_responsibility": {
            "source": "shadow_projection.deterministic_account",
            "visible_credit_btc": account.get("visible_credit_btc"),
            "fee_btc": account.get("fee_btc"),
            "c_minus_f_btc": account.get("c_minus_f_btc"),
            "scenario_liability": deepcopy(account.get("scenario_liability") or []),
            "quote_fee_only_no_mu_or_trade_permission": True,
        },
        "risk_status": risk_summary,
        "risk_reference_status": deepcopy(risk_summary),
        "counter_catalog_gap_reassess": _counter_catalog_gap_reassess(projection),
        "release_identity": _release_identity(release_identity),
        "composition_inputs": composition,
        "qualification_input": {
            "packet_sequence_empty": len(packet_rows_input) == 0,
            "current_packet_built_from_snapshot": built_current,
            "qualification_report_count_received": len(reports_input),
            "qualification_report_count_applied": len(reports_for_window),
            "standalone_reports_can_promote_native_or_window": False,
            "native_requalification_requires_source_bytes": True,
            "native_source_hashes_available": sorted(native_source_map),
        },
        "source_bindings": {
            "current_packet": deepcopy(current_packet),
            "window_evidence": deepcopy(window),
            "shadow_projection": deepcopy(projection),
        },
        "validity": _validity(),
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    if include_routing:
        if __package__:
            from . import astra_evidence_routing_v1 as routing
        else:
            import astra_evidence_routing_v1 as routing
        body["evidence_routing"] = routing.build_routing(snapshot, body)
    return _seal_review_evidence(body)


def validate_review_evidence(evidence: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Replay package hashes and snapshot binding without market recomputation."""
    if evidence.get("schema") not in {SCHEMA, *LEGACY_SCHEMAS, *COMPAT_SCHEMAS}:
        raise ValueError("review_evidence_schema_mismatch")
    if evidence.get("role") != ROLE:
        raise ValueError("review_evidence_role_mismatch")
    _require_review_evidence_hash(evidence)
    _require_schema_shape(evidence)

    snapshot_copy = _safe_copy(snapshot, "snapshot")
    composition = evidence.get("composition_inputs") if isinstance(evidence.get("composition_inputs"), Mapping) else None
    if not isinstance(composition, Mapping):
        raise ValueError("composition_inputs_missing")
    _require_hash(composition, "composition_inputs_hash")
    rebuilt = build_review_evidence(
        snapshot_copy,
        packet_sequence=composition.get("packet_sequence") or (),
        qualification_reports=composition.get("qualification_reports") or (),
        cross_section=composition.get("cross_section") or (),
        risk_reference=composition.get("risk_reference"),
        release_identity=composition.get("release_identity"),
        native_sources=_decode_native_sources(composition.get("native_sources_b64") or {}),
        include_routing=evidence.get("schema") == SCHEMA,
    )
    for field in (
        "package_identity", "candidate_id", "snapshot_id", "decision_asof_ms", "packet_hash",
        "current_cross_section", "quote_fee_deterministic_responsibility",
        "risk_status", "risk_reference_status", "counter_catalog_gap_reassess", "release_identity",
        "composition_inputs", "qualification_input", "source_bindings", "validity", "execution_allowed",
        "llm_http_calls", "inference_calls", "evidence_routing",
    ):
        if evidence.get(field) != rebuilt.get(field):
            raise ValueError(field + "_rebuild_mismatch")
    same_window = evidence.get("same_candidate_window")
    if not isinstance(same_window, Mapping):
        raise ValueError("same_candidate_window_missing")
    expected_same_window = _legacy_same_candidate_window_expected(
        same_window,
        rebuilt.get("same_candidate_window") if isinstance(rebuilt.get("same_candidate_window"), Mapping) else {},
    )
    if same_window != expected_same_window:
        raise ValueError("same_candidate_window_rebuild_mismatch")

    bindings = evidence.get("source_bindings") if isinstance(evidence.get("source_bindings"), Mapping) else {}
    packet = bindings.get("current_packet")
    window = bindings.get("window_evidence")
    projection = bindings.get("shadow_projection")
    if not isinstance(packet, Mapping) or not isinstance(window, Mapping) or not isinstance(projection, Mapping):
        raise ValueError("review_evidence_source_bindings_missing")

    packets.replay_packet(packet)
    _require_hash(window, "window_evidence_hash")
    _require_hash(projection, "projection_hash")

    if packet.get("snapshot") != snapshot_copy:
        raise ValueError("review_evidence_snapshot_mismatch")
    if evidence.get("snapshot_id") != snapshot_copy.get("snapshot_id"):
        raise ValueError("review_evidence_snapshot_id_mismatch")
    if evidence.get("candidate_id") != snapshot_copy.get("candidate_id"):
        raise ValueError("review_evidence_candidate_id_mismatch")
    if evidence.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("review_evidence_packet_hash_mismatch")

    current = evidence.get("current_cross_section") if isinstance(evidence.get("current_cross_section"), Mapping) else {}
    if current.get("snapshot_id") != packet["snapshot"]["snapshot_id"]:
        raise ValueError("current_cross_section_snapshot_mismatch")
    if current.get("candidate_id") != packet["snapshot"]["candidate_id"]:
        raise ValueError("current_cross_section_candidate_mismatch")
    if current.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("current_cross_section_packet_hash_mismatch")

    same_window = evidence.get("same_candidate_window") if isinstance(evidence.get("same_candidate_window"), Mapping) else {}
    if same_window.get("source_window_evidence_hash") != window.get("window_evidence_hash"):
        raise ValueError("same_candidate_window_source_hash_mismatch")
    if window.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("window_packet_hash_mismatch")
    if window.get("snapshot_id") != packet["snapshot"]["snapshot_id"]:
        raise ValueError("window_snapshot_mismatch")
    if projection.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("projection_packet_hash_mismatch")
    if projection.get("window_evidence_hash") != window.get("window_evidence_hash"):
        raise ValueError("projection_window_hash_mismatch")

    risk_status = evidence.get("risk_status") if isinstance(evidence.get("risk_status"), Mapping) else {}
    if risk_status.get("production_mu_ref_btc") is not None or risk_status.get("production_M_ref_btc") is not None:
        raise ValueError("production_mu_M_must_remain_null")
    if not risk_status.get("mu_M_null_reason"):
        raise ValueError("mu_M_null_reason_missing")

    validity = evidence.get("validity") if isinstance(evidence.get("validity"), Mapping) else {}
    if evidence.get("execution_allowed") is not False or validity.get("execution_allowed") is not False:
        raise ValueError("execution_permission_changed")
    if validity.get("review_gate_changed") is not False or validity.get("financial_gate_changed") is not False:
        raise ValueError("gate_changed")
    if evidence.get("llm_http_calls") != 0 or evidence.get("inference_calls") != 0:
        raise ValueError("forbidden_calls_present")

    result = {
        "schema": VALIDATION_SCHEMA,
        "status": "VALID",
        "candidate_id": evidence.get("candidate_id"),
        "snapshot_id": evidence.get("snapshot_id"),
        "packet_hash": packet.get("packet_hash"),
        "window_evidence_hash": window.get("window_evidence_hash"),
        "projection_hash": projection.get("projection_hash"),
        "evidence_hash": evidence.get("evidence_hash"),
        "review_evidence_hash": evidence.get("review_evidence_hash"),
        "checks": {
            "self_hash": True,
            "packet_replay": True,
            "snapshot_binding": True,
            "window_projection_binding": True,
            "composition_rebuild": True,
            "no_execution_or_gate_change": True,
            "mu_M_null": True,
        },
        "execution_allowed": False,
        "llm_http_calls": 0,
        "inference_calls": 0,
    }
    return _seal(result, "validation_hash")

