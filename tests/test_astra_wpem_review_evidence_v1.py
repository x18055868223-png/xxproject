import copy
import json
from pathlib import Path

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_wpem_native_v1 as native
from tools import astra_wpem_review_evidence_v1 as review
from tests.test_astra_wpem_native_v1 import native_fixture


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "wpem_rc"
ROUND2 = FIXTURE_ROOT / "wpem_round2"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _synthetic_packet_report(offset=0, *, price=100000, short_bid=None, long_ask=None, quantity=0.1, side="put"):
    packet, source = native_fixture(offset=offset, quantity=quantity)
    raw = copy.deepcopy(packet["raw_payload"])
    if side == "call":
        raw["candidate"]["short_instrument"] = "BTC-26SEP26-103000-C"
        raw["candidate"]["long_instrument"] = "BTC-26SEP26-105000-C"
        raw["metadata"]["short"].update(instrument_name="BTC-26SEP26-103000-C", option_type="call", strike=103000)
        raw["metadata"]["long"].update(instrument_name="BTC-26SEP26-105000-C", option_type="call", strike=105000)
    if side == "call":
        identity = packets.core.candidate_identity(raw["candidate"], raw["metadata"], raw["market"]["valuation_ts_ms"])
        manifest = copy.deepcopy(raw["data_identity"]["source_manifest"])
        manifest["candidate_id"] = identity["candidate_id"]
        manifest["manifest_hash"] = packets.core._hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
        raw["data_identity"]["source_manifest"] = manifest
    raw["market"]["reference_price_usd"] = price
    for leg in ("short", "long"):
        raw["market"][leg + "_book"]["index_price"] = price
    if short_bid is not None:
        raw["market"]["short_book"]["bids"][0][0] = short_bid
        raw["market"]["short_book"]["asks"][0][0] = short_bid + 0.001
    if long_ask is not None:
        raw["market"]["long_book"]["asks"][0][0] = long_ask
        raw["market"]["long_book"]["bids"][0][0] = max(long_ask - 0.001, 0.0001)
    snapshot = packets.core.evaluate(raw)
    updated = packets.build_packet(snapshot, raw_payload=raw)
    report = native.qualify_native(updated, source, evidence_kind="synthetic_fixture")
    if side == "put":
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


def _reseal(row, key):
    return native.seal({k: v for k, v in row.items() if k != key}, key)


def _reseal_review(row):
    body = {k: copy.deepcopy(v) for k, v in row.items() if k not in {"evidence_hash", "review_evidence_hash"}}
    digest = packets.core._hash(body)
    body["evidence_hash"] = digest
    body["review_evidence_hash"] = digest
    return body


def _coverage_windows_from_source(evidence):
    return [
        {key: copy.deepcopy(row.get(key)) for key in review.COVERAGE_SUMMARY_FIELDS}
        for row in evidence["source_bindings"]["window_evidence"]["coverage"]
    ]


def _legacy_evidence_without_coverage_summary(evidence):
    legacy = copy.deepcopy(evidence)
    legacy.pop("evidence_routing", None)
    legacy["schema"] = "wpem_review_evidence@1.0.0"
    legacy["same_candidate_window"]["schema"] = "wpem_review_same_candidate_window@1.0.0"
    legacy["same_candidate_window"].pop("coverage_summary", None)
    return _reseal_review(legacy)


def _cross_section_row(packet, role="current"):
    return {
        "candidate_id": packet["snapshot"]["candidate_id"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "packet_hash": packet["packet_hash"],
        "row_role": role,
        "source": "fixture_cross_section",
    }


def _package_for_sequence(packet_rows, reports):
    current = packet_rows[-1]
    return review.build_review_evidence(
        current["snapshot"],
        packet_sequence=packet_rows,
        qualification_reports=reports,
        cross_section=[_cross_section_row(current)],
    )


def test_builds_review_evidence_with_nested_shadow_window_and_replay_validation():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]

    evidence = review.build_review_evidence(
        current["snapshot"],
        packet_sequence=packets_,
        qualification_reports=reports,
        cross_section=[_cross_section_row(current)],
    )
    validation = review.validate_review_evidence(evidence, current["snapshot"])

    assert evidence["schema"] == "wpem_review_evidence@1.1.0"
    assert evidence["role"] == "candidate"
    assert evidence["package_identity"]["kind"] == "PRODUCT_REVIEW_EVIDENCE_PACKAGE"
    assert evidence["evidence_hash"] == evidence["review_evidence_hash"]
    assert evidence["same_candidate_window"]["source_schema"] == "wpem_window_evidence@1.0.0-shadow"
    assert evidence["source_bindings"]["window_evidence"]["schema"] == "wpem_window_evidence@1.0.0-shadow"
    assert evidence["same_candidate_window"]["status"] == "OBSERVED"
    assert evidence["same_candidate_window"]["qualification"]["status"] == "SYNTHETIC_WINDOW_COVERED"
    assert evidence["same_candidate_window"]["LevelDeltaPath"]["path"]["status"] == "PATH_OBSERVED"
    assert evidence["same_candidate_window"]["LevelDeltaPath"]["path"]["win_probability"] is None
    assert evidence["current_cross_section"]["packet_hash"] == current["packet_hash"]
    assert evidence["quote_fee_deterministic_responsibility"]["quote_fee_only_no_mu_or_trade_permission"] is True
    assert evidence["risk_status"]["production_mu_ref_btc"] is None
    assert evidence["risk_status"]["production_M_ref_btc"] is None
    assert evidence["risk_status"]["mu_M_null_reason"] == "REFERENCE_ANALYSIS_MISSING"
    assert evidence["validity"]["financial_gate_changed"] is False
    assert evidence["execution_allowed"] is False
    assert validation["status"] == "VALID"
    assert validation["checks"]["window_projection_binding"] is True


def test_coverage_summary_copies_raw_window_coverage_without_private_refs():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    summary = evidence["same_candidate_window"]["coverage_summary"]

    assert evidence["schema"] == "wpem_review_evidence@1.1.0"
    assert evidence["same_candidate_window"]["schema"] == "wpem_review_same_candidate_window@1.0.1"
    assert summary["schema"] == "wpem_window_coverage_summary@1.0.0"
    assert summary["windows"] == _coverage_windows_from_source(evidence)
    assert summary["windows"][0]["status"] == "SYNTHETIC_WINDOW_COVERED"
    assert summary["windows"][0]["native_qualified_points"] == 0
    assert all("point_refs" not in row for row in summary["windows"])
    assert summary["policy"]["raw_point_refs_excluded"] is True
    assert summary["policy"]["raw_source_bytes_excluded"] is True
    assert summary["policy"]["does_not_change_financial_or_review_gates"] is True
    assert review.validate_review_evidence(evidence, packets_[-1]["snapshot"])["status"] == "VALID"


def test_insufficient_window_coverage_summary_preserves_source_status_and_counts():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]
    evidence = review.build_review_evidence(
        current["snapshot"],
        packet_sequence=[current],
        qualification_reports=[reports[-1]],
        cross_section=[_cross_section_row(current)],
    )
    summary = evidence["same_candidate_window"]["coverage_summary"]

    assert summary["windows"] == _coverage_windows_from_source(evidence)
    assert summary["windows"][0]["status"] == "INSUFFICIENT_WINDOW"
    assert summary["windows"][0]["actual_coverage_ms"] == 0
    assert summary["windows"][0]["point_count"] == 1
    assert summary["windows"][0]["native_qualified_points"] == 0
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"


def test_method_break_keeps_coverage_summary_as_copied_diagnostic_only():
    packets_, reports = _covered_synthetic_sequence()
    broken_reports = copy.deepcopy(reports)
    broken_reports[1]["comparability_hash"] = "0" * 64
    broken_reports[1] = _reseal(broken_reports[1], "qualification_hash")

    evidence = review.build_review_evidence(packets_[-1]["snapshot"], packets_, broken_reports)
    summary = evidence["same_candidate_window"]["coverage_summary"]

    assert evidence["same_candidate_window"]["source_method_breaks"]["method_break_observed"] is True
    assert summary["windows"] == _coverage_windows_from_source(evidence)
    assert summary["policy"]["does_not_change_window_qualification"] is True
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"


def test_same_current_candidate_can_have_different_coverage_summary():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]

    covered = _package_for_sequence(packets_, reports)
    current_only = review.build_review_evidence(
        current["snapshot"],
        packet_sequence=[current],
        qualification_reports=[reports[-1]],
        cross_section=[_cross_section_row(current)],
    )

    assert covered["candidate_id"] == current_only["candidate_id"]
    assert covered["snapshot_id"] == current_only["snapshot_id"]
    assert covered["packet_hash"] == current_only["packet_hash"]
    assert covered["same_candidate_window"]["coverage_summary"]["windows"] != (
        current_only["same_candidate_window"]["coverage_summary"]["windows"]
    )
    assert covered["same_candidate_window"]["coverage_summary"]["windows"] == _coverage_windows_from_source(covered)
    assert current_only["same_candidate_window"]["coverage_summary"]["windows"] == (
        _coverage_windows_from_source(current_only)
    )


def test_historical_real_blocked_fixture_carries_exact_coverage_summary_without_upgrade():
    packet = _json(ROUND2 / "evidence" / "historical_real_put_packet.json")
    report = _json(ROUND2 / "evidence" / "put_native_qualification.json")

    evidence = review.build_review_evidence(
        packet["snapshot"],
        packet_sequence=[packet],
        qualification_reports=[report],
    )
    summary = evidence["same_candidate_window"]["coverage_summary"]

    assert summary["windows"] == _coverage_windows_from_source(evidence)
    assert summary["windows"][0]["status"] == "INSUFFICIENT_WINDOW"
    assert summary["windows"][0]["native_qualified_points"] == 0
    assert evidence["same_candidate_window"]["qualification"]["real_window_status"] == "REAL_WINDOW_BLOCKED"
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"


def test_empty_packet_sequence_builds_current_packet_and_ignores_standalone_reports():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]

    evidence = review.build_review_evidence(current["snapshot"], qualification_reports=reports)

    assert evidence["qualification_input"]["packet_sequence_empty"] is True
    assert evidence["qualification_input"]["current_packet_built_from_snapshot"] is True
    assert evidence["qualification_input"]["qualification_report_count_received"] == 4
    assert evidence["qualification_input"]["qualification_report_count_applied"] == 0
    assert evidence["qualification_input"]["standalone_reports_can_promote_native_or_window"] is False
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"
    assert evidence["same_candidate_window"]["qualification"]["status"] == "INSUFFICIENT_WINDOW"
    assert evidence["same_candidate_window"]["LevelDeltaPath"]["path"]["status"] == "NO_PATH_DUE_TO_WINDOW_GAP"
    assert review.validate_review_evidence(evidence, current["snapshot"])["status"] == "VALID"


def test_candidate_rotation_is_reported_without_market_trend_claim():
    old_packets, old_reports = _covered_synthetic_sequence()
    rotated, rotated_report = _synthetic_packet_report(
        1_200_040, price=101500, short_bid=0.0065, long_ask=0.0040, quantity=1.0
    )
    packet_sequence = old_packets + [rotated]
    reports = old_reports + [rotated_report]

    evidence = review.build_review_evidence(rotated["snapshot"], packet_sequence, reports)
    breaks = evidence["same_candidate_window"]["source_method_breaks"]

    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"
    assert breaks["candidate_rotation_observed"] is True
    assert len(breaks["candidate_ids_seen"]) == 2
    assert breaks["market_trend_claim_allowed"] is False
    assert evidence["same_candidate_window"]["LevelDeltaPath"]["delta"]["status"] == "NO_STRICT_DELTA"


def test_method_break_is_reported_without_market_trend_claim():
    packets_, reports = _covered_synthetic_sequence()
    broken_reports = copy.deepcopy(reports)
    broken_reports[1]["comparability_hash"] = "0" * 64
    broken_reports[1] = _reseal(broken_reports[1], "qualification_hash")

    evidence = review.build_review_evidence(packets_[-1]["snapshot"], packets_, broken_reports)
    breaks = evidence["same_candidate_window"]["source_method_breaks"]

    assert breaks["method_break_observed"] is True
    assert breaks["market_trend_claim_allowed"] is False
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"


def test_resealed_native_report_cannot_promote_without_real_native_requalification():
    packets_, reports = _covered_synthetic_sequence()
    bad_reports = copy.deepcopy(reports)
    bad_reports[-1]["status"] = "NATIVE_QUALIFIED"
    bad_reports[-1]["evidence_kind"] = "native_producer"
    bad_reports[-1]["gaps"] = []
    bad_reports[-1]["contract_qualified"] = True
    bad_reports[-1] = _reseal(bad_reports[-1], "qualification_hash")

    with pytest.raises(ValueError, match="window_report_native_.*mismatch"):
        review.build_review_evidence(packets_[-1]["snapshot"], packets_, bad_reports, native_sources={})


def test_tampered_top_level_binding_is_rejected_even_if_rehashed():
    packets_, reports = _covered_synthetic_sequence()
    evidence = review.build_review_evidence(packets_[-1]["snapshot"], packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["current_cross_section"]["candidate_id"] = "tampered"
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="current_cross_section_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_wrong_snapshot_validation_is_rejected():
    packets_, reports = _covered_synthetic_sequence()
    evidence = review.build_review_evidence(packets_[-1]["snapshot"], packets_, reports)
    other_packet, _other_report = _synthetic_packet_report(1_500_050, quantity=1.0)

    with pytest.raises(ValueError, match="candidate_id_rebuild_mismatch"):
        review.validate_review_evidence(evidence, other_packet["snapshot"])


def test_catalog_counters_gaps_reassess_and_release_identity_are_carried_for_audit():
    packets_, reports = _covered_synthetic_sequence()
    evidence = review.build_review_evidence(packets_[-1]["snapshot"], packets_, reports)
    catalog = evidence["counter_catalog_gap_reassess"]

    assert catalog["catalog_policy"]["unknown_important_counter"] == "CATALOG_GAP"
    assert {row["id"] for row in catalog["counterclaims"]} >= {
        "counter.risk.reference_not_qualified",
        "counter.compensation.expected_payout_unknown",
    }
    assert "gap.compensation.mu_reference_missing" in {row["id"] for row in catalog["gaps"]}
    assert "reassess.new_quote_or_window" in {row["id"] for row in catalog["reassess_conditions"]}
    assert evidence["release_identity"]["status"] == "NOT_PROVIDED_NOT_BLOCKING"
    assert evidence["release_identity"]["loaded_source_hash_or_account_inventory_required"] is False


def test_cross_section_row_must_match_current_snapshot_identity_and_clock():
    packets_, reports = _covered_synthetic_sequence()
    bad_row = _cross_section_row(packets_[-1])
    bad_row["decision_asof_ms"] += 1

    with pytest.raises(ValueError, match="cross_section_identity_mismatch:decision_asof_ms"):
        review.build_review_evidence(
            packets_[-1]["snapshot"], packets_, reports, cross_section=[bad_row]
        )


def test_resealed_financial_numeric_tamper_is_rejected_by_rebuild():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["quote_fee_deterministic_responsibility"]["visible_credit_btc"] = 999
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="quote_fee_deterministic_responsibility_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_resealed_path_tamper_is_rejected_by_rebuild():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["same_candidate_window"]["LevelDeltaPath"]["path"]["classification"] = "persistent_same_zone"
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="same_candidate_window_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_resealed_coverage_summary_tamper_is_rejected_by_rebuild():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["same_candidate_window"]["coverage_summary"]["windows"][0]["actual_coverage_ms"] += 1
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="same_candidate_window_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_legacy_100_review_evidence_without_coverage_summary_still_replays_exact_shape():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]
    evidence = _package_for_sequence(packets_, reports)
    legacy = _legacy_evidence_without_coverage_summary(evidence)

    assert legacy["schema"] == "wpem_review_evidence@1.0.0"
    assert legacy["same_candidate_window"]["schema"] == "wpem_review_same_candidate_window@1.0.0"
    assert "coverage_summary" not in legacy["same_candidate_window"]
    assert review.validate_review_evidence(legacy, current["snapshot"])["status"] == "VALID"


def test_legacy_100_top_level_cannot_carry_new_window_or_coverage_shape():
    packets_, reports = _covered_synthetic_sequence()
    current = packets_[-1]
    mixed = copy.deepcopy(_package_for_sequence(packets_, reports))
    mixed.pop("evidence_routing", None)  # Isolate the older window-shape downgrade.
    mixed["schema"] = "wpem_review_evidence@1.0.0"
    mixed = _reseal_review(mixed)

    assert mixed["same_candidate_window"]["schema"] == "wpem_review_same_candidate_window@1.0.1"
    assert "coverage_summary" in mixed["same_candidate_window"]
    with pytest.raises(ValueError, match="legacy_review_evidence_shape_mismatch"):
        review.validate_review_evidence(mixed, current["snapshot"])


def test_resealed_catalog_tamper_is_rejected_by_rebuild():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["counter_catalog_gap_reassess"]["counterclaims"][0]["id"] = "counter.fake"
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="counter_catalog_gap_reassess_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_resealed_nested_window_path_tamper_is_rejected_by_rebuild():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    tampered = copy.deepcopy(evidence)
    tampered["source_bindings"]["window_evidence"]["path"]["classification"] = "persistent_same_zone"
    tampered["source_bindings"]["window_evidence"] = _reseal(
        tampered["source_bindings"]["window_evidence"], "window_evidence_hash"
    )
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="source_bindings_rebuild_mismatch"):
        review.validate_review_evidence(tampered, packets_[-1]["snapshot"])


def test_same_current_cross_section_can_have_different_same_candidate_paths_without_probability():
    favorable_rows = [
        _synthetic_packet_report(0, price=98500, short_bid=0.0100, long_ask=0.0040),
        _synthetic_packet_report(300_010, price=99000, short_bid=0.0090, long_ask=0.0040),
        _synthetic_packet_report(600_020, price=99500, short_bid=0.0080, long_ask=0.0040),
        _synthetic_packet_report(900_030, price=100000, short_bid=0.0075, long_ask=0.0040),
    ]
    adverse_rows = [
        _synthetic_packet_report(0, price=101500, short_bid=0.0100, long_ask=0.0040),
        _synthetic_packet_report(300_010, price=101000, short_bid=0.0090, long_ask=0.0040),
        _synthetic_packet_report(600_020, price=100500, short_bid=0.0080, long_ask=0.0040),
        _synthetic_packet_report(900_030, price=100000, short_bid=0.0075, long_ask=0.0040),
    ]
    fav_packets, fav_reports = [row[0] for row in favorable_rows], [row[1] for row in favorable_rows]
    adv_packets, adv_reports = [row[0] for row in adverse_rows], [row[1] for row in adverse_rows]

    favorable = _package_for_sequence(fav_packets, fav_reports)
    adverse = _package_for_sequence(adv_packets, adv_reports)

    assert favorable["current_cross_section"] == adverse["current_cross_section"]
    fav_path = favorable["same_candidate_window"]["LevelDeltaPath"]["path"]
    adv_path = adverse["same_candidate_window"]["LevelDeltaPath"]["path"]
    assert fav_path["classification"] == "single_direction_favorable_relief"
    assert adv_path["classification"] == "single_direction_adverse_pressure"
    assert fav_path["win_probability"] is None
    assert adv_path["win_probability"] is None


def test_q1_favorable_path_with_visible_credit_decline_is_preserved_as_delta_only():
    packets_, reports = _covered_synthetic_sequence()
    evidence = _package_for_sequence(packets_, reports)
    level_delta_path = evidence["same_candidate_window"]["LevelDeltaPath"]

    assert level_delta_path["path"]["classification"] == "single_direction_favorable_relief"
    assert level_delta_path["delta"]["changes"]["visible_credit_btc_delta"] < 0
    assert evidence["risk_status"]["positive_economic_bias_allowed"] is False
    assert level_delta_path["path"]["win_probability"] is None


def test_q1_adverse_path_with_visible_credit_increase_is_preserved_as_delta_only():
    rows = [
        _synthetic_packet_report(0, price=101000, short_bid=0.0070, long_ask=0.0040),
        _synthetic_packet_report(300_010, price=100500, short_bid=0.0080, long_ask=0.0040),
        _synthetic_packet_report(600_020, price=100000, short_bid=0.0090, long_ask=0.0040),
        _synthetic_packet_report(900_030, price=99500, short_bid=0.0100, long_ask=0.0040),
    ]
    packets_, reports = [row[0] for row in rows], [row[1] for row in rows]
    evidence = _package_for_sequence(packets_, reports)
    level_delta_path = evidence["same_candidate_window"]["LevelDeltaPath"]

    assert level_delta_path["path"]["classification"] == "single_direction_adverse_pressure"
    assert level_delta_path["delta"]["changes"]["visible_credit_btc_delta"] > 0
    assert evidence["risk_status"]["positive_economic_bias_allowed"] is False
    assert level_delta_path["path"]["win_probability"] is None


def test_cross_section_accepts_same_asof_peer_snapshot_as_comparable_without_ranking():
    current, current_report = _synthetic_packet_report(0, price=100000, side="put")
    peer, _peer_report = _synthetic_packet_report(0, price=100000, side="call")

    evidence = review.build_review_evidence(
        current["snapshot"],
        packet_sequence=[current],
        qualification_reports=[current_report],
        cross_section=[_cross_section_row(current), {"snapshot": peer["snapshot"], "source": "peer_snapshot"}],
    )
    rows = evidence["current_cross_section"]["cross_section_rows"]
    peer_row = next(row for row in rows if row["role"] == "PEER")

    assert peer_row["comparability"] == "COMPARABLE_PEER"
    assert peer_row["same_decision_asof"] is True
    assert peer_row["contract"]["side"] == "call"
    assert peer_row["candidate_id"] == peer["snapshot"]["candidate_id"]
    assert peer_row["not_used_for_window"] is True
    assert peer_row["not_ranked_or_scored"] is True
    assert evidence["current_cross_section"]["asof_join_rule"].find("cannot fill same_candidate_window") >= 0
    assert review.validate_review_evidence(evidence, current["snapshot"])["status"] == "VALID"


def test_cross_section_marks_old_peer_snapshot_non_comparable_without_changing_clock():
    current, current_report = _synthetic_packet_report(300_000, price=100000, side="put")
    peer, _peer_report = _synthetic_packet_report(0, price=100000, side="call")

    evidence = review.build_review_evidence(
        current["snapshot"],
        packet_sequence=[current],
        qualification_reports=[current_report],
        cross_section=[{"snapshot": peer["snapshot"], "source": "old_peer"}],
    )
    peer_row = evidence["current_cross_section"]["cross_section_rows"][0]

    assert peer_row["comparability"] == "NON_COMPARABLE_SNAPSHOT_ASOF"
    assert peer_row["same_decision_asof"] is False
    assert peer_row["decision_asof_ms"] == peer["cutoff"]["decision_asof_ms"]
    assert evidence["current_cross_section"]["decision_asof_ms"] == current["cutoff"]["decision_asof_ms"]
    assert peer_row["not_used_for_window"] is True
    assert review.validate_review_evidence(evidence, current["snapshot"])["status"] == "VALID"


def test_cross_section_peer_claimed_identity_mismatch_is_rejected():
    current, current_report = _synthetic_packet_report(0, price=100000, side="put")
    peer, _peer_report = _synthetic_packet_report(0, price=100000, side="call")
    bad_claim = {"snapshot": peer["snapshot"], "candidate_id": "wrong"}

    with pytest.raises(ValueError, match="cross_section_peer_claim_mismatch:candidate_id"):
        review.build_review_evidence(
            current["snapshot"], [current], [current_report], cross_section=[bad_claim]
        )


def test_cross_section_future_peer_snapshot_is_rejected():
    current, current_report = _synthetic_packet_report(0, price=100000, side="put")
    future_peer, _peer_report = _synthetic_packet_report(300_000, price=100000, side="call")

    with pytest.raises(ValueError, match="cross_section_peer_future_asof"):
        review.build_review_evidence(
            current["snapshot"], [current], [current_report], cross_section=[{"snapshot": future_peer["snapshot"]}]
        )


def test_cross_section_peer_snapshot_tamper_is_caught_by_composition_rebuild():
    current, current_report = _synthetic_packet_report(0, price=100000, side="put")
    peer, _peer_report = _synthetic_packet_report(0, price=100000, side="call")
    evidence = review.build_review_evidence(
        current["snapshot"], [current], [current_report], cross_section=[{"snapshot": peer["snapshot"]}]
    )
    tampered = copy.deepcopy(evidence)
    tampered["current_cross_section"]["cross_section_rows"][0]["contract"]["side"] = "put"
    tampered = _reseal_review(tampered)

    with pytest.raises(ValueError, match="current_cross_section_rebuild_mismatch"):
        review.validate_review_evidence(tampered, current["snapshot"])
