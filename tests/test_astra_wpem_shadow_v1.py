import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_underwriting_fact_context_v20 as facts
from tools import astra_wpem_native_v1 as native
from tools import astra_wpem_shadow_v1 as shadow
from tests.test_astra_wpem_native_v1 import native_fixture, T


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "wpem_rc"
ROUND2 = FIXTURE_ROOT / "wpem_round2"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("status", ["NATIVE_QUALIFIED", "SYNTHETIC_CONTRACT_PASSED"])
def test_legacy_report_schema_cannot_be_promoted_by_rehash(status):
    packet = _json(ROUND2 / "evidence" / "historical_real_put_packet.json")
    report = _json(ROUND2 / "evidence" / "put_native_qualification.json")
    report["status"] = status
    report["contract_qualified"] = True
    report["gaps"] = []
    report = native.seal({k: v for k, v in report.items() if k != "qualification_hash"}, "qualification_hash")
    with pytest.raises(ValueError, match="window_report_schema_or_status_invalid"):
        shadow.build_window_evidence([packet], [report])


def _synthetic_packet_report(offset=0, *, price=100000, short_bid=None, long_ask=None, quantity=0.1, risk_mu=None):
    packet, source = native_fixture(offset=offset, quantity=quantity)
    raw = copy.deepcopy(packet["raw_payload"])
    raw["market"]["reference_price_usd"] = price
    for leg in ("short", "long"):
        raw["market"][leg + "_book"]["index_price"] = price
    if short_bid is not None:
        raw["market"]["short_book"]["bids"][0][0] = short_bid
        raw["market"]["short_book"]["asks"][0][0] = short_bid + 0.001
    if long_ask is not None:
        raw["market"]["long_book"]["asks"][0][0] = long_ask
        raw["market"]["long_book"]["bids"][0][0] = max(long_ask - 0.001, 0.0001)
    if risk_mu is not None:
        identity = packets.core.candidate_identity(raw["candidate"], raw["metadata"], raw["market"]["valuation_ts_ms"])
        horizon_hours = (identity["expiry_ms"] - raw["market"]["valuation_ts_ms"]) / 3_600_000
        raw["risk"] = {"status": "supported", "support_verified": True,
                       "candidate_id": identity["candidate_id"], "unit": "BTC",
                       "model_id": "synthetic-test-only",
                       "model_hash": hashlib.sha256(b"fixture").hexdigest(),
                       "calibration_id": "synthetic-test-only",
                       "support_scope": "synthetic-fixture-only",
                       "source_kind": "synthetic_fixture",
                       "input_ts_ms": raw["market"]["valuation_ts_ms"],
                       "feature_end_ts_ms": raw["market"]["valuation_ts_ms"],
                       "reference_price_usd": price,
                       "input_row_hash": hashlib.sha256(b"synthetic-input").hexdigest(),
                       "normalization_basis": "native_btc_fixture_direct",
                       "training_cutoff": "synthetic-not-trained",
                       "label_known_cutoff": "synthetic-not-trained",
                       "quantity_btc": quantity, "horizon_hours": horizon_hours, "mu_btc": risk_mu}
    snapshot = packets.core.evaluate(raw)
    updated = packets.build_packet(snapshot, raw_payload=raw)
    report = native.qualify_native(updated, source, evidence_kind="synthetic_fixture")
    assert report["status"] == "SYNTHETIC_CONTRACT_PASSED"
    return updated, report


def _packet_with_source_facts(offset=0, *, price=100000, anchor=100000, short_bid=None, long_ask=None):
    packet, source = native_fixture(offset=offset)
    raw = copy.deepcopy(packet["raw_payload"])
    now = raw["market"]["valuation_ts_ms"]
    raw["market"]["reference_price_usd"] = price
    for leg in ("short", "long"):
        raw["market"][leg + "_book"]["index_price"] = price
    if short_bid is not None:
        raw["market"]["short_book"]["bids"][0][0] = short_bid
        raw["market"]["short_book"]["asks"][0][0] = short_bid + 0.001
    if long_ask is not None:
        raw["market"]["long_book"]["asks"][0][0] = long_ask
        raw["market"]["long_book"]["bids"][0][0] = max(long_ask - 0.001, 0.0001)

    record = json.loads(source)
    record["decision"]["runtime_facts"]["current_price"] = price
    record["decision"]["runtime_facts"]["current_price_observed_ms"] = now - 1000
    source_ts = now - 5000
    record["factor_snapshot"] = {
        "anchor": {
            "effective_flip_point": anchor,
            "anchor_gravity_ref_score": 72.0,
            "gex_freshness": "FRESH",
        },
        "gamma_regime": {
            "regime": "TRANSITION",
            "net_gex_sign": "POSITIVE",
            "net_gamma_notional": 4200000.0,
            "gex_info_agrees": True,
            "data_state": "OK",
            "reason_codes": ["fixture_transition"],
        },
        "gex_info": {
            "market_state": "positive_gamma",
            "total_net_gex": 4200000.0,
            "support_walls": [97000.0, 95000.0],
            "resistance_walls": [103000.0, 105000.0],
            "fetched_at_ms": source_ts,
            "age_ms": 4000,
        },
        "space_gate": {
            "state": "STRUCTURE_ELIGIBLE",
            "observed_at_ms": now,
            "since_ts_ms": now - 300000,
            "reason_codes": [],
            "anchor": {"state": "Valid", "score": 72.0, "source_ts_ms": source_ts, "freshness": "FRESH", "ok": True},
            "net_gamma": {
                "value": 4200000.0,
                "unit": "USD",
                "sign": "POSITIVE",
                "source": "gexmonitorapi.gex_board.total_net_gex",
                "source_ts_ms": source_ts,
                "valid": True,
                "transition": False,
                "conflict": False,
                "ok": True,
            },
        },
    }
    source_bytes = json.dumps(record, sort_keys=True).encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    raw["fmz_fact_context"] = facts.from_evaluation_snapshot(record, now, source_hash=source_sha)
    manifest = copy.deepcopy(raw["data_identity"]["source_manifest"])
    manifest["source_records"][0]["version"]["payload_hash"] = source_sha
    manifest["source_records"][0]["content"]["values"]["source_hash"] = source_sha
    manifest["manifest_hash"] = packets.core._hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    raw["data_identity"]["source_manifest"] = manifest
    snapshot = packets.core.evaluate(raw)
    updated = packets.build_packet(snapshot, raw_payload=raw)
    report = native.qualify_native(updated, source_bytes, evidence_kind="synthetic_fixture")
    assert report["status"] == "SYNTHETIC_CONTRACT_PASSED"
    return updated, report


def _packet_with_retained_source(offset, source_record, source_bytes, source_sha, *, price=100000, anchor=100000):
    packet, _source = native_fixture(offset=offset)
    raw = copy.deepcopy(packet["raw_payload"])
    now = raw["market"]["valuation_ts_ms"]
    raw["market"]["reference_price_usd"] = price
    for leg in ("short", "long"):
        raw["market"][leg + "_book"]["index_price"] = price
    raw["fmz_fact_context"] = facts.from_evaluation_snapshot(json.loads(source_bytes), now, source_hash=source_sha)
    manifest = copy.deepcopy(raw["data_identity"]["source_manifest"])
    retained = copy.deepcopy(source_record)
    retained["content"]["values"]["source_hash"] = source_sha
    retained["version"]["payload_hash"] = source_sha
    manifest["source_records"] = [retained]
    manifest["cutoff_at_ms"] = now
    snapshot0 = packets.core.evaluate(raw)
    manifest["candidate_id"] = snapshot0["candidate_id"]
    manifest["manifest_hash"] = packets.core._hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    raw["data_identity"]["source_manifest"] = manifest
    snapshot = packets.core.evaluate(raw)
    updated = packets.build_packet(snapshot, raw_payload=raw)
    report = native.qualify_native(updated, source_bytes, evidence_kind="synthetic_fixture")
    assert report["status"] == "SYNTHETIC_CONTRACT_PASSED"
    return updated, report


def _covered_synthetic_sequence():
    rows = [
        _synthetic_packet_report(0, price=99500, short_bid=0.0100, long_ask=0.0040),
        _synthetic_packet_report(300_010, price=100000, short_bid=0.0090, long_ask=0.0040),
        _synthetic_packet_report(600_020, price=100500, short_bid=0.0080, long_ask=0.0040),
        _synthetic_packet_report(900_030, price=101000, short_bid=0.0070, long_ask=0.0040),
    ]
    return [row[0] for row in rows], [row[1] for row in rows]


def _projection(packet, reports, packets_=None, reference=None, legacy=None):
    seq = packets_ or [packet]
    window = shadow.build_window_evidence(seq, reports, candidate_id=packet["snapshot"]["candidate_id"],
                                          cutoff_ms=packet["cutoff"]["decision_asof_ms"])
    return window, shadow.build_shadow_projection(packet, window, reference_analysis=reference,
                                                  legacy_review_input=legacy)


def test_synthetic_window_projects_q1_q2_without_mu_or_permission():
    packet_rows, reports = _covered_synthetic_sequence()
    current = packet_rows[-1]

    window, projection = _projection(current, reports, packets_=packet_rows)
    payload = shadow.required_selection_payload(projection)
    validated = shadow.validate_shadow_selection(current, payload, projection)
    q = shadow.project_q1_q2(current, projection, validated)

    assert window["path"]["classification"] == "single_direction_favorable_relief"
    assert projection["response_protocol"] == shadow.RESPONSE_PROTOCOL
    assert projection["risk_qualification"]["terminal_status"] == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
    assert projection["deterministic_account"]["mu_ref_btc"] is None
    assert q["q2"]["expected_payout_status"] == "UNKNOWN"
    assert q["q2"]["positive_economic_bias_allowed"] is False
    assert q["q2"]["q1_can_improve_while_q2_worsens"] is False
    assert q["q2"]["economic_comparison_status"] == "UNKNOWN"
    assert q["execution_allowed"] is False
    assert q["llm_http_calls"] == q["inference_calls"] == 0
    assert q["q1"]["level_delta_path"]["path"]["win_probability"] is None


def test_same_anchor_with_opposite_paths_changes_only_path_not_probability_or_bias():
    favorable_rows = [
        _packet_with_source_facts(0, price=98500, anchor=100000, short_bid=0.0100, long_ask=0.0040),
        _packet_with_source_facts(300_010, price=99000, anchor=100000, short_bid=0.0090, long_ask=0.0040),
        _packet_with_source_facts(600_020, price=99500, anchor=100000, short_bid=0.0080, long_ask=0.0040),
        _packet_with_source_facts(900_030, price=100000, anchor=100000, short_bid=0.0075, long_ask=0.0040),
    ]
    adverse_rows = [
        _packet_with_source_facts(0, price=101500, anchor=100000, short_bid=0.0100, long_ask=0.0040),
        _packet_with_source_facts(300_010, price=101000, anchor=100000, short_bid=0.0090, long_ask=0.0040),
        _packet_with_source_facts(600_020, price=100500, anchor=100000, short_bid=0.0080, long_ask=0.0040),
        _packet_with_source_facts(900_030, price=100000, anchor=100000, short_bid=0.0075, long_ask=0.0040),
    ]

    fav_packets, fav_reports = [row[0] for row in favorable_rows], [row[1] for row in favorable_rows]
    adv_packets, adv_reports = [row[0] for row in adverse_rows], [row[1] for row in adverse_rows]
    fav_window, fav_projection = _projection(fav_packets[-1], fav_reports, packets_=fav_packets)
    adv_window, adv_projection = _projection(adv_packets[-1], adv_reports, packets_=adv_packets)

    fav_q = shadow.project_q1_q2(
        fav_packets[-1],
        fav_projection,
        shadow.validate_shadow_selection(
            fav_packets[-1], shadow.required_selection_payload(fav_projection), fav_projection),
    )
    adv_q = shadow.project_q1_q2(
        adv_packets[-1],
        adv_projection,
        shadow.validate_shadow_selection(
            adv_packets[-1], shadow.required_selection_payload(adv_projection), adv_projection),
    )

    assert fav_window["level"]["anchor_gex_wall_group"]["details"]["anchor"]["effective_flip_point"] == 100000
    assert adv_window["level"]["anchor_gex_wall_group"]["details"]["anchor"]["effective_flip_point"] == 100000
    assert fav_window["level"]["gamma_regime"]["value"]["regime"] == "TRANSITION"
    assert adv_window["level"]["gamma_regime"]["value"]["regime"] == "TRANSITION"
    assert fav_window["path"]["classification"] != adv_window["path"]["classification"]
    assert fav_window["path"]["win_probability"] is None
    assert adv_window["path"]["win_probability"] is None
    assert fav_q["q2"]["positive_economic_bias_allowed"] is False
    assert adv_q["q2"]["positive_economic_bias_allowed"] is False
    assert fav_q["q2"]["expected_payout_status"] == "UNKNOWN"
    assert adv_q["q2"]["expected_payout_status"] == "UNKNOWN"


def test_repeated_same_source_record_counts_reads_not_independent_updates_or_confirmations():
    base_packet, base_report = _packet_with_source_facts(0, price=99500, anchor=100000)
    base_record = base_packet["raw_payload"]["data_identity"]["source_manifest"]["source_records"][0]
    base_source_hash = base_report["source_hash"]
    base_source = copy.deepcopy(base_packet["raw_payload"]["fmz_fact_context"])
    # Retain the current native fixture envelope instead of reconstructing
    # an old producer/card envelope in this repeated-source test.
    _packet, fixture_bytes = native_fixture()
    retained_source = json.loads(fixture_bytes)
    retained_source["decision"]["runtime_facts"].update({
        "current_price": 99500,
        "current_price_observed_ms": base_source["price_observed_ms"],
    })
    retained_source["factor_snapshot"] = {
        "anchor": {"effective_flip_point": 100000, "anchor_gravity_ref_score": 72.0, "gex_freshness": "FRESH"},
        "gamma_regime": {"regime": "TRANSITION", "net_gex_sign": "POSITIVE", "net_gamma_notional": 4200000.0,
                         "gex_info_agrees": True, "data_state": "OK", "reason_codes": ["fixture_transition"]},
        "gex_info": {"market_state": "positive_gamma", "total_net_gex": 4200000.0},
    }
    base_source_bytes = json.dumps(retained_source, sort_keys=True).encode("utf-8")
    base_source_hash = hashlib.sha256(base_source_bytes).hexdigest()
    rows = [
        _packet_with_retained_source(0, base_record, base_source_bytes, base_source_hash, price=99500),
        _packet_with_retained_source(60_000, base_record, base_source_bytes, base_source_hash, price=100000),
        _packet_with_retained_source(120_000, base_record, base_source_bytes, base_source_hash, price=100500),
        _packet_with_retained_source(240_000, base_record, base_source_bytes, base_source_hash, price=101000),
    ]
    packets_, reports = [row[0] for row in rows], [row[1] for row in rows]

    window = shadow.build_window_evidence(packets_, reports)
    groups = window["source_dependency_diagnostics"]["source_record_groups"]
    assert len(groups) == 1
    assert groups[0]["read_count"] == 4
    assert groups[0]["unique_update_count"] == 1
    assert window["level"]["anchor_gex_wall_group"]["independent_confirmation_count"] <= 1


def test_synthetic_account_fixture_can_show_q1_improves_q2_worsens_without_b_qualification():
    rows = [
        _synthetic_packet_report(0, price=99500, short_bid=0.0100, long_ask=0.0040, risk_mu=0.00020),
        _synthetic_packet_report(300_010, price=100000, short_bid=0.0090, long_ask=0.0040, risk_mu=0.00017),
        _synthetic_packet_report(600_020, price=100500, short_bid=0.0080, long_ask=0.0040, risk_mu=0.00014),
        _synthetic_packet_report(900_030, price=101000, short_bid=0.0070, long_ask=0.0040, risk_mu=0.00010),
    ]
    packet_rows, reports = [row[0] for row in rows], [row[1] for row in rows]
    current = packet_rows[-1]

    _window, projection = _projection(current, reports, packets_=packet_rows)
    payload = shadow.required_selection_payload(projection)
    validated = shadow.validate_shadow_selection(current, payload, projection)
    q = shadow.project_q1_q2(current, projection, validated)

    assert projection["risk_qualification"]["terminal_status"] == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
    assert projection["deterministic_account"]["account_fixture_kind"] == "SYNTHETIC_ACCOUNT_FIXTURE"
    assert q["q1"]["risk_reference_consumed"] is False
    assert q["q2"]["economic_comparison_status"] == "SYNTHETIC_ACCOUNT_FIXTURE"
    assert q["q2"]["M_ref_delta_btc"] < 0
    assert q["q2"]["q1_can_improve_while_q2_worsens"] is True
    assert q["q2"]["expected_payout_status"] == "UNKNOWN"
    assert q["q2"]["positive_economic_bias_allowed"] is False
    assert q["execution_allowed"] is False


def test_actual_b_reference_analysis_is_nested_hash_and_packet_bound():
    packet = _json(ROUND2 / "evidence" / "historical_real_put_packet.json")
    report = _json(ROUND2 / "evidence" / "put_native_qualification.json")
    reference = _json(ROUND2 / "risk_qualification" / "put" / "reference_analysis.json")

    window = shadow.build_window_evidence([packet], [report])
    projection = shadow.build_shadow_projection(packet, window, reference_analysis=reference)

    assert window["completeness"]["real_window_status"] == "REAL_WINDOW_BLOCKED"
    assert window["independent_trade_count"] is None
    assert projection["risk_qualification"]["terminal_status"] == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
    assert projection["risk_qualification"]["required_coverage"] == {"available": 2, "total": 10, "missing": 8}
    assert projection["risk_qualification"]["target_status"] == "TARGET_BINDING_UNPROVEN"
    assert projection["deterministic_account"]["mu_ref_btc"] is None
    assert projection["deterministic_account"]["M_ref_btc"] is None
    assert projection["deterministic_account"]["c_minus_f_btc"] == packet["snapshot"]["economics"]["loss_budget_btc"]
    assert window["source_dependency_diagnostics"]["reread_is_not_update"] is True

    terminal_bad = copy.deepcopy(reference)
    terminal_bad["terminal_status"] = "QUALIFIED_SHADOW_REFERENCE_ONLY"
    terminal_bad = native.seal({k: v for k, v in terminal_bad.items() if k != "reference_analysis_hash"},
                               "reference_analysis_hash")
    with pytest.raises(ValueError, match="terminal_status"):
        shadow.build_shadow_projection(packet, window, reference_analysis=terminal_bad)

    bad = copy.deepcopy(reference)
    bad["qualification_report"]["required_missing_count"] = 0
    bad = native.seal({k: v for k, v in bad.items() if k != "reference_analysis_hash"}, "reference_analysis_hash")
    with pytest.raises(ValueError, match="hash_mismatch"):
        shadow.build_shadow_projection(packet, window, reference_analysis=bad)

    rebound = copy.deepcopy(reference)
    rebound["qualification_report"]["packet_hash"] = "0" * 64
    rebound["qualification_report"] = native.seal(
        {k: v for k, v in rebound["qualification_report"].items() if k != "qualification_report_hash"},
        "qualification_report_hash",
    )
    rebound = native.seal({k: v for k, v in rebound.items() if k != "reference_analysis_hash"},
                          "reference_analysis_hash")
    with pytest.raises(ValueError, match="reference_packet_hash_mismatch"):
        shadow.build_shadow_projection(packet, window, reference_analysis=rebound)


def test_same_expiry_put_call_count_as_one_expiry_event_not_two_trades():
    put_packet = _json(ROUND2 / "evidence" / "historical_real_put_packet.json")
    call_packet = _json(ROUND2 / "evidence" / "historical_real_call_packet.json")
    put_report = _json(ROUND2 / "evidence" / "put_native_qualification.json")
    call_report = _json(ROUND2 / "evidence" / "call_native_qualification.json")

    window = shadow.build_window_evidence(
        [put_packet, call_packet],
        [put_report, call_report],
        candidate_id=call_packet["snapshot"]["candidate_id"],
        cutoff_ms=call_packet["cutoff"]["decision_asof_ms"],
    )

    counts = window["source_dependency_diagnostics"]["dependency_counts"]
    assert counts["snapshots"] == 2
    assert counts["candidates"] == 2
    assert counts["expiry_groups"] == 1
    assert counts["side_groups"] == 2
    assert window["independent_trade_count"] is None


def test_legacy_review_input_must_rebuild_exactly_and_old_protocol_is_unchanged():
    packet = _json(ROUND2 / "evidence" / "historical_real_put_packet.json")
    report = _json(ROUND2 / "evidence" / "put_native_qualification.json")
    legacy = _json(ROUND2 / "evidence" / "legacy_put_review_input.json")
    reference = _json(ROUND2 / "risk_qualification" / "put" / "reference_analysis.json")

    _window, projection = _projection(packet, [report], reference=reference, legacy=legacy)

    legacy_binding = projection["source_bindings"]["legacy_review_input"]
    assert legacy_binding["response_protocol"] == "bounded_claim_ids@2.4.7"
    assert legacy_binding["projection_hash"] == legacy["projection_hash"]
    assert legacy_binding["claim_catalog_hash"] == legacy["claim_catalog_hash"]

    tampered = copy.deepcopy(legacy)
    tampered["projection_hash"] = "x"
    with pytest.raises(ValueError, match="legacy_review_input_rebuild_mismatch"):
        _projection(packet, [report], reference=reference, legacy=tampered)


def test_new_window_projection_hash_invalidates_old_selection():
    packet_rows, reports = _covered_synthetic_sequence()
    current = packet_rows[-1]
    _window1, projection1 = _projection(current, reports, packets_=packet_rows)
    payload = shadow.required_selection_payload(projection1)

    sparse_window = shadow.build_window_evidence(
        [packet_rows[0], current],
        [reports[0], reports[-1]],
        candidate_id=current["snapshot"]["candidate_id"],
        cutoff_ms=current["cutoff"]["decision_asof_ms"],
    )
    projection2 = shadow.build_shadow_projection(current, sparse_window)

    assert projection1["projection_hash"] != projection2["projection_hash"]
    with pytest.raises(shadow.ShadowValidationError, match="projection hash"):
        shadow.validate_shadow_selection(current, payload, projection2)


def test_window_gap_and_candidate_rotation_do_not_create_path():
    first, first_report = _synthetic_packet_report(0)
    rotated, rotated_report = _synthetic_packet_report(900_000, quantity=1)

    window = shadow.build_window_evidence(
        [first, rotated],
        [first_report, rotated_report],
        candidate_id=rotated["snapshot"]["candidate_id"],
        cutoff_ms=rotated["cutoff"]["decision_asof_ms"],
    )

    assert window["source_breaks"]["candidate_rotation_observed"] is True
    assert window["path"]["status"] == "NO_PATH_DUE_TO_WINDOW_GAP"
    assert window["path"]["win_probability"] is None


def test_future_other_candidate_packet_is_rejected_after_current_is_selected():
    current, current_report = _synthetic_packet_report(0)
    future_other, future_report = _synthetic_packet_report(900_000, quantity=1)

    with pytest.raises(ValueError, match="future_packet"):
        shadow.build_window_evidence(
            [current, future_other],
            [current_report, future_report],
            candidate_id=current["snapshot"]["candidate_id"],
            cutoff_ms=current["cutoff"]["decision_asof_ms"],
        )


def test_covered_older_window_without_current_report_is_insufficient():
    rows = [
        _synthetic_packet_report(0),
        _synthetic_packet_report(300_010),
        _synthetic_packet_report(600_020),
        _synthetic_packet_report(900_030),
    ]
    packet_rows, reports = [row[0] for row in rows], [row[1] for row in rows]
    current = packet_rows[-1]

    window = shadow.build_window_evidence(
        packet_rows,
        reports[:-1],
        candidate_id=current["snapshot"]["candidate_id"],
        cutoff_ms=current["cutoff"]["decision_asof_ms"],
    )

    assert window["coverage"][0]["status"] == "INSUFFICIENT_WINDOW"
    assert window["completeness"]["current_report_in_selected_window"] is False
    assert window["path"]["status"] == "NO_PATH_DUE_TO_WINDOW_GAP"


def test_rehashed_qualification_report_cannot_join_wrong_candidate_or_asof():
    packet, report = _synthetic_packet_report(0)
    wrong_candidate = copy.deepcopy(report)
    wrong_candidate["candidate_id"] = "wrong-candidate"
    wrong_candidate = native.seal(
        {k: v for k, v in wrong_candidate.items() if k != "qualification_hash"},
        "qualification_hash",
    )
    with pytest.raises(ValueError, match="candidate_mismatch"):
        shadow.build_window_evidence([packet], [wrong_candidate])

    wrong_asof = copy.deepcopy(report)
    wrong_asof["decision_asof_ms"] += 1
    wrong_asof = native.seal({k: v for k, v in wrong_asof.items() if k != "qualification_hash"},
                             "qualification_hash")
    with pytest.raises(ValueError, match="asof_mismatch"):
        shadow.build_window_evidence([packet], [wrong_asof])


@pytest.mark.parametrize('kind,status', [
    ('native_producer', 'NATIVE_QUALIFIED'),
    ('synthetic_fixture', 'NATIVE_QUALIFIED'),
    ('historical_public_capture', 'SYNTHETIC_CONTRACT_PASSED'),
    ('synthetic_fixture', 'BLOCKED'),
])
def test_resealed_synthetic_report_cannot_promote_native_window(kind, status):
    packet_rows, reports = _covered_synthetic_sequence()
    forged = []
    for report in reports:
        body = copy.deepcopy({k: v for k, v in report.items() if k != 'qualification_hash'})
        body.update(evidence_kind=kind, status=status, contract_qualified=True, gaps=[])
        forged.append(native.seal(body, 'qualification_hash'))
    with pytest.raises(ValueError, match='window_report_.*(provenance|kind|contract)_mismatch'):
        shadow.build_window_evidence(packet_rows, forged)


@pytest.mark.parametrize('side', ['put', 'call'])
def test_resealed_historical_blocked_report_cannot_claim_native_without_source(side):
    packet = _json(ROUND2 / f'evidence/historical_real_{side}_packet.json')
    report = _json(ROUND2 / f'evidence/{side}_native_qualification.json')
    body = {k: copy.deepcopy(v) for k, v in report.items() if k != 'qualification_hash'}
    body.update(schema=native.SCHEMA, evidence_kind='native_producer', status='NATIVE_QUALIFIED',
                contract_qualified=True, gaps=[])
    with pytest.raises(ValueError, match='window_report_native_source_bytes_missing'):
        shadow.build_window_evidence([packet], [native.seal(body, 'qualification_hash')])


def test_native_contract_shape_requires_matching_full_source_requalification():
    # This is a fabricated API contract shape, never a real native observation.
    fixture, source_bytes = native_fixture()
    raw = copy.deepcopy(fixture['raw_payload'])
    raw['data_identity']['kind'] = 'live_public_capture'
    raw['market']['reference_source'] = 'deribit_index'
    for leg in ('short', 'long'):
        raw['market'][leg + '_book']['index_price'] = raw['market']['reference_price_usd']
    packet = packets.build_packet(packets.core.evaluate(raw), raw_payload=raw)
    report = native.qualify_native(packet, source_bytes, evidence_kind='native_producer')
    assert report['status'] == 'NATIVE_QUALIFIED'
    with pytest.raises(ValueError, match='native_source_bytes_missing'):
        shadow.build_window_evidence([packet], [report])
    with pytest.raises(ValueError, match='native_requalification_mismatch'):
        shadow.build_window_evidence([packet], [report], native_sources={report['source_hash']: b'{}'})
    window = shadow.build_window_evidence([packet], [report], native_sources={report['source_hash']: source_bytes})
    assert window['completeness']['real_window_status'] == 'REAL_WINDOW_BLOCKED'
    assert window['coverage'][0]['native_qualified_points'] == 1
    forged = {k: copy.deepcopy(v) for k, v in report.items() if k != 'qualification_hash'}
    forged['original_native_clocks']['ingested_at'] = forged['decision_asof_ms'] + 1
    with pytest.raises(ValueError, match='native_requalification_mismatch'):
        shadow.build_window_evidence([packet], [native.seal(forged, 'qualification_hash')],
                                     native_sources={report['source_hash']: source_bytes})


def test_catalog_gap_wrong_role_and_explained_with_missing_mu_are_rejected():
    packet_rows, reports = _covered_synthetic_sequence()
    current = packet_rows[-1]
    _window, projection = _projection(current, reports, packets_=packet_rows)

    unknown = shadow.required_selection_payload(projection)
    unknown["selected_counter_ids"] = ["counter.not_in_catalog"]
    with pytest.raises(shadow.ShadowValidationError, match="CATALOG_GAP"):
        shadow.validate_shadow_selection(current, unknown, projection)

    wrong_role = shadow.required_selection_payload(projection)
    wrong_role["selected_counter_ids"] = wrong_role["selected_claim_ids"]
    with pytest.raises(shadow.ShadowValidationError, match="wrong role"):
        shadow.validate_shadow_selection(current, wrong_role, projection)

    explained = shadow.required_selection_payload(projection, applicability="EXPLAINED")
    with pytest.raises(shadow.ShadowValidationError, match="missing mu"):
        shadow.validate_shadow_selection(current, explained, projection)
