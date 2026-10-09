import copy
import hashlib
import json
import pytest
from tools import astra_wpem_native_v1 as native
from tools import astra_underwriting_fact_context_v20 as facts
from tests.test_astra_candidate_asof_v1 import snapshot_at, T


def native_fixture(offset=0, quantity=0.1):
    """Synthetic test shape, explicitly never a real producer observation."""
    now = T + offset
    _, raw = snapshot_at(now, quantity=quantity)
    raw.setdefault("data_identity", {"kind": "synthetic_fixture"})
    source = {"schema_name": "EvaluationSnapshot", "schema_version": "nrd.schema.v1.0.0",
              "source_available_at_ms": now-5, "ingested_at_ms": now-3,
              "snapshot_exported_ms": now-3,
              "clock_provenance": {
                  "schema": "native_snapshot_clock_provenance@1.0.0",
                  "source_available_at_ms": "snapshot_payload_handoff_before_export_envelope",
                  "ingested_at_ms": "producer_snapshot_exporter_acceptance",
                  "snapshot_exported_ms": "producer_export_attempt_before_atomic_write",
                  "not_remote_receiver_ingestion": True,
                  "not_filesystem_visibility_proof": True,
                  "not_market_source_ingestion": True},
              "decision": {"demo_version": "2.0.2", "symbol": "BTC", "ts_ms": now-1000,
                           "runtime_facts": {"runtime_mode": "live_public_read_only",
                              "current_price": 100000, "current_price_source": "fixture",
                              "current_price_observed_ms": now-1000}},
              "current_snapshot_context": {
                  "schema": "current_evaluation_context@1.0.0",
                  "source": "current_evaluation_snapshot", "observed_at_ms": now-1000,
                  "phase": "PHASE_0_OBSERVE_ONLY", "affects_confidence": False,
                  "affects_blocking": False, "affects_trade_allowed": False,
                  "session_context": {
                  "schema": "SignalSessionPremiseDurabilityContext@1.0.0",
                  "clock_window": "fixture", "backtest_delta_pp": 0,
                  "observed_at_ms": now-1000, "premise_durability": "fixture",
                  "validation_basis": {"kind": "fixture"}},
                  "temporal_durability": "fixture"}}
    source_bytes = json.dumps(source, sort_keys=True).encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    raw["fmz_fact_context"] = facts.from_evaluation_snapshot(source, now, source_hash=source_sha)
    raw["metadata_received_ts_ms"] = {"short": now-2, "long": now-2}
    snapshot = native.packets.core.evaluate(raw)
    manifest = {"cutoff_at_ms": now, "candidate_id": snapshot["candidate_id"], "source_records": [{
        "record_id": "fixture-native-"+str(now), "identity": {"product_id": "fmz.bridge.snapshots.v1"},
        "time": {"observation_end_ms": now-1000, "first_seen_at_ms": now-5,
                 "retrieved_at_ms": now-3, "ingested_at_ms": now-3},
        "version": {"payload_hash": source_sha}, "content": {"values": {"source_hash": source_sha}}}]}
    manifest["manifest_hash"] = native.packets.core._hash(manifest)
    raw["data_identity"]["source_manifest"] = manifest
    snapshot = native.packets.core.evaluate(raw)
    return native.packets.build_packet(snapshot, raw_payload=raw), source_bytes


def report_at(offset=0, quantity=0.1):
    packet, source = native_fixture(offset, quantity)
    return native.qualify_native(packet, source, evidence_kind="synthetic_fixture")


def test_synthetic_shape_passes_contract_but_cannot_be_native():
    report = report_at()
    assert report["contract_qualified"] is True
    assert report["status"] == "SYNTHETIC_CONTRACT_PASSED"
    packet, source = native_fixture()
    assert native.qualify_native(packet, source, evidence_kind="native_producer")["status"] == "BLOCKED"


@pytest.mark.parametrize("kind", ["compat", "reconstructed", "historical_public_capture"])
def test_compat_or_reconstructed_capture_cannot_be_promoted(kind):
    packet, source = native_fixture()
    assert native.qualify_native(packet, source, evidence_kind=kind)["status"] == "BLOCKED"


def test_missing_native_clock_is_not_backfilled_by_receipt():
    packet, source = native_fixture()
    record = json.loads(source)
    del record["source_available_at_ms"]
    result = native.qualify_native(packet, json.dumps(record).encode(), evidence_kind="synthetic_fixture",
                                  acquisition_receipt={"retrieved_at_ms": T-1, "ingested_at_ms": T-1})
    assert result["original_native_clocks"]["source_available_at"] is None
    assert "NATIVE_CLOCK_MISSING:source_available_at" in result["gaps"]
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize("source", [b"{", b"\xff", b"[]", b"null", "not-bytes",
                                   b'{"source_available_at_ms": NaN}',
                                   b'{"ingested_at_ms": Infinity}'])
def test_malformed_native_source_produces_sealed_blocked_report(source):
    packet, _ = native_fixture()
    report = native.qualify_native(packet, source, evidence_kind="synthetic_fixture")
    assert "NATIVE_SOURCE_BYTES_MALFORMED" in report["gaps"]
    assert report["status"] == "BLOCKED"
    native.require_hash(report, "qualification_hash")


@pytest.mark.parametrize("field", ["decision", "runtime_facts"])
def test_malformed_native_decision_is_auditable(field):
    packet, source = native_fixture()
    record = json.loads(source)
    if field == "decision":
        record["decision"] = ["invalid"]
        expected = "NATIVE_DECISION_MALFORMED"
    else:
        record["decision"]["runtime_facts"] = "invalid"
        expected = "NATIVE_RUNTIME_FACTS_MALFORMED"
    report = native.qualify_native(packet, json.dumps(record).encode(), evidence_kind="synthetic_fixture")
    assert expected in report["gaps"]
    assert report["status"] == "BLOCKED"
    native.require_hash(report, "qualification_hash")


@pytest.mark.parametrize("field,name", [("source_available_at_ms", "source_available_at"),
                                        ("ingested_at_ms", "ingested_at"),
                                        ("ts_ms", "observation_time")])
@pytest.mark.parametrize("value", [True, "invalid", 1.5, -1])
def test_invalid_native_clock_does_not_escape_the_blocked_report(field, name, value):
    packet, source = native_fixture()
    record = json.loads(source)
    if field == "ts_ms":
        record["decision"][field] = value
    else:
        record[field] = value
    report = native.qualify_native(packet, json.dumps(record).encode(), evidence_kind="synthetic_fixture")
    assert "NATIVE_CLOCK_INVALID:" + name in report["gaps"]
    assert report["status"] == "BLOCKED"
    native.require_hash(report, "qualification_hash")


@pytest.mark.parametrize("receipt", [[], {"ingested_at_ms": True}, {"retrieved_at_ms": "invalid"},
                                    {"ingested_at_ms": float("nan")}])
def test_malformed_receipt_cannot_supply_or_upgrade_native_clocks(receipt):
    packet, source = native_fixture()
    report = native.qualify_native(packet, source, evidence_kind="synthetic_fixture",
                                   acquisition_receipt=receipt)
    assert any(gap.startswith(("ACQUISITION_RECEIPT_MALFORMED", "RECEIPT_CLOCK_INVALID:"))
               for gap in report["gaps"])
    assert report["status"] == "BLOCKED"
    native.require_hash(report, "qualification_hash")


@pytest.mark.parametrize("fault,expected", [
    ("old_producer", "NATIVE_PRODUCER_VERSION_MISMATCH"),
    ("historical_context_only", "NATIVE_CURRENT_CONTEXT_BINDING_INVALID"),
    ("old_context_time", "NATIVE_CURRENT_CONTEXT_BINDING_INVALID"),
    ("remote_clock_role", "NATIVE_CLOCK_PROVENANCE_INVALID"),
    ("integer_boolean_role", "NATIVE_CLOCK_PROVENANCE_INVALID"),
    ("missing_export_clock", "NATIVE_EXPORT_CLOCK_INVALID"),
    ("nonobject_context", "NATIVE_CURRENT_CONTEXT_BINDING_INVALID"),
])
def test_new_native_contract_rejects_old_context_or_mislabelled_clocks(fault, expected):
    packet, source = native_fixture()
    record = json.loads(source)
    if fault == "old_producer":
        record["decision"]["demo_version"] = "2.0.1"
    elif fault == "historical_context_only":
        record["signal_review_card"] = record.pop("current_snapshot_context")
    elif fault == "old_context_time":
        record["current_snapshot_context"]["observed_at_ms"] -= 1
    elif fault == "remote_clock_role":
        record["clock_provenance"]["ingested_at_ms"] = "server_receiver_ingestion"
    elif fault == "integer_boolean_role":
        record["clock_provenance"]["not_remote_receiver_ingestion"] = 1
    elif fault == "missing_export_clock":
        del record["snapshot_exported_ms"]
    elif fault == "nonobject_context":
        record["current_snapshot_context"] = []
    result = native.qualify_native(packet, json.dumps(record).encode(), evidence_kind="synthetic_fixture")
    assert expected in result["gaps"]
    assert result["contract_qualified"] is False


@pytest.mark.parametrize("field", ["source_available_at_ms", "ingested_at_ms"])
def test_native_future_clock_rejected_even_if_source_hash_recomputed(field):
    packet, source = native_fixture()
    record = json.loads(source)
    record[field] = T+1
    result = native.qualify_native(packet, json.dumps(record).encode(), evidence_kind="synthetic_fixture")
    assert any(gap.startswith("NATIVE_FUTURE_CLOCK:") for gap in result["gaps"])
    assert result["contract_qualified"] is False


def test_at_least_three_points_15m_and_10m_gap_are_fixed_requirements():
    reports = [report_at(x) for x in (0, 300_010, 600_020, 900_030)]
    window = native.qualified_windows(reports, reports[0]["candidate_id"], T+900_030)
    assert window["windows"][0]["status"] == "SYNTHETIC_WINDOW_COVERED"
    assert window["windows"][0]["native_qualified_points"] == 0
    short = native.qualified_windows(reports[:3], reports[0]["candidate_id"], T+600_020)
    assert short["windows"][0]["status"] == "INSUFFICIENT_WINDOW"
    sparse = native.qualified_windows([reports[0], reports[-1]], reports[0]["candidate_id"], T+900_030)
    assert sparse["windows"][0]["status"] == "INSUFFICIENT_WINDOW"
    assert window["windows"][1]["status"] == "INSUFFICIENT_WINDOW"


def test_rotations_and_method_boundary_are_not_joined():
    old = report_at()
    rotated = report_at(900_000, quantity=1)
    assert old["candidate_id"] != rotated["candidate_id"]
    result = native.qualified_windows([old, rotated], rotated["candidate_id"], T+900_000)
    assert result["windows"][0]["point_count"] == 1
    newer = report_at(900_000)
    changed = copy.deepcopy(newer)
    changed["comparability_hash"] = "new-method"
    changed = native.seal({k:v for k,v in changed.items() if k != "qualification_hash"}, "qualification_hash")
    result = native.qualified_windows([old, changed], old["candidate_id"], T+900_000)
    assert result["method_break_observed"] is True
    assert result["windows"][0]["status"] == "INSUFFICIENT_WINDOW"


def test_cutoff_cannot_extend_ten_minutes_of_observations_into_fifteen():
    reports = [report_at(x) for x in (0, 300_000, 600_000)]
    result = native.qualified_windows(reports, reports[0]["candidate_id"], T+900_000)
    assert result["windows"][0]["actual_coverage_ms"] == 600_000
    assert result["windows"][0]["status"] == "INSUFFICIENT_WINDOW"


def test_future_window_and_hash_tampering_fail():
    first, future = report_at(), report_at(300_000)
    with pytest.raises(ValueError, match="future_qualified"):
        native.qualified_windows([first, future], first["candidate_id"], T)
    first["status"] = "NATIVE_QUALIFIED"
    with pytest.raises(ValueError, match="hash_mismatch"):
        native.qualified_windows([first], first["candidate_id"], T)


@pytest.mark.parametrize("field", ["fact_content", "manifest_content"])
def test_rehashed_source_content_cannot_borrow_original_native_hash(field):
    packet, source = native_fixture()
    raw = copy.deepcopy(packet["raw_payload"])
    if field == "fact_content":
        raw["fmz_fact_context"]["current_price_usd"] += 1
        expected = "NATIVE_FACT_CONTENT_MISMATCH"
    else:
        manifest = raw["data_identity"]["source_manifest"]
        manifest["source_records"][0]["content"]["values"]["source_hash"] = "e"*64
        manifest["manifest_hash"] = native.packets.core._hash({
            key:value for key,value in manifest.items() if key != "manifest_hash"})
        expected = "NATIVE_MANIFEST_SOURCE_BINDING_MISSING"
    updated = native.packets.build_packet(native.packets.core.evaluate(raw), raw_payload=raw)
    report = native.qualify_native(updated, source, evidence_kind="synthetic_fixture")
    assert expected in report["gaps"]
    assert report["contract_qualified"] is False
