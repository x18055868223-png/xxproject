import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_underwriting_v20 as core
from tests.test_astra_underwriting_v20 import sample, T
from tests import test_astra_workbench_v20 as fx


def synthetic_quote_capture(*args, **kwargs):
    payload = fx.fake_quote_capture(*args, **kwargs)
    payload["data_identity"]["kind"] = "synthetic_fixture"
    payload["market"]["reference_source"] = "synthetic_fixture"
    return payload


def synthetic_workbench(tmp_path):
    wb, path = fx.make_workbench(tmp_path, api_key=None)
    wb.quote_capture = synthetic_quote_capture
    return wb, path


def snapshot_at(now=T, side="put", quantity=0.1):
    payload = sample(side, risk=False)
    payload["candidate"]["quantity_btc"] = quantity
    payload["market"].update(valuation_ts_ms=now, received_ts_ms=now)
    for leg in ("short", "long"):
        payload["market"][leg + "_book"]["timestamp"] = now
        for direction in ("bids", "asks"):
            payload["market"][leg + "_book"][direction][0][1] = quantity
    return core.evaluate(payload), payload


@pytest.mark.parametrize("side,quantity", [("put", 0.1), ("put", 1), ("call", 0.1), ("call", 1)])
def test_same_packet_offline_replays_native_quantity_and_keeps_missing_risk(side, quantity):
    snapshot, raw = snapshot_at(side=side, quantity=quantity)
    before = copy.deepcopy(snapshot)
    packet = packets.build_packet(snapshot, raw_payload=raw)
    loaded = json.loads(json.dumps(packet))
    assert packets.replay_packet(loaded) == packet["replay"]
    assert packet["replay"]["raw_quote_status"] == "raw_leg_books_replayed"
    assert packet["replay"]["quantity_btc"] == quantity
    assert packet["risk"] == snapshot["risk"]
    assert packet["replay"]["mu_ref_btc"] is None
    assert packet["inference_calls"] == packet["llm_http_calls"] == 0
    assert snapshot == before
    if side == "put":
        assert max(row["payout_btc"] for row in packet["valuation"]["liability"]["payout_table"]) > snapshot["liability"]["btc_reference_scale_at_spot"]


def test_hash_and_rehashed_amount_tampering_are_rejected():
    snapshot, _ = snapshot_at()
    packet = packets.build_packet(snapshot)
    packet["valuation"]["economics"]["visible_credit_btc"] = 100
    with pytest.raises(ValueError, match="hash_mismatch"):
        packets.replay_packet(packet)
    packet["packet_hash"] = core._hash({k: v for k, v in packet.items() if k != "packet_hash"})
    with pytest.raises(ValueError, match="valuation_mismatch"):
        packets.replay_packet(packet)


@pytest.mark.parametrize("field,value", [("amount_btc", 0.1), ("basis", "actual"), ("covers", ["entry"])])
def test_raw_fee_conflict_is_rejected(field, value):
    snapshot, raw = snapshot_at()
    raw["fee"][field] = value
    with pytest.raises(ValueError, match="raw_payload_fee_mismatch"):
        packets.build_packet(snapshot, raw_payload=raw)


@pytest.mark.parametrize("leg", ["short", "long"])
def test_future_raw_instrument_metadata_receipt_is_rejected(leg):
    snapshot, raw = snapshot_at()
    raw["metadata_received_ts_ms"] = {"short": T, "long": T}
    raw["metadata_received_ts_ms"][leg] = T + 1
    with pytest.raises(ValueError, match="future_instrument_metadata_receipt"):
        packets.build_packet(snapshot, raw_payload=raw)


@pytest.mark.parametrize("clocks,missing", [({"short": T}, True), ({"short": T, "long": T}, False)])
def test_partial_raw_metadata_receipt_remains_a_gap(clocks, missing):
    snapshot, raw = snapshot_at()
    raw["metadata_received_ts_ms"] = clocks
    packet = packets.build_packet(snapshot, raw_payload=raw)
    assert ("original_instrument_metadata_receipt_clock_not_retained" in packet["gaps"]) == missing


@pytest.mark.parametrize("same_id", [True, False])
def test_conflicting_source_identity_is_rejected_instead_of_last_write_wins(same_id):
    snapshot, _ = snapshot_at()
    first = {"record_id": "one", "identity": {"product_id": "test", "scope": "same"},
             "time": {"observation_end_ms": T, "first_seen_at_ms": T},
             "version": {"payload_hash": "a" * 64}, "content": {"values": {"value": 1}}}
    second = copy.deepcopy(first)
    second["record_id"] = "one" if same_id else "two"
    second["content"]["values"]["value"] = 2
    manifest = {"cutoff_at_ms": T, "candidate_id": snapshot["candidate_id"], "source_records": [first, second]}
    manifest["manifest_hash"] = core._hash(manifest)
    snapshot["data_identity"]["source_manifest"] = manifest
    snapshot["snapshot_id"] = core._hash({k: snapshot[k] for k in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")})
    with pytest.raises(ValueError, match="source_.*conflict"):
        packets.build_packet(snapshot)


def test_candidate_price_basis_change_breaks_window():
    first, _ = snapshot_at()
    old = packets.build_packet(first)
    later, raw = snapshot_at(T + 300_000)
    raw["market"]["reference_source"] = "external_index_snapshot"
    raw["data_identity"] = {"kind": "manual_input"}
    later = core.evaluate(raw)
    assert later["candidate_id"] == first["candidate_id"]
    history = [{"candidate_id": first["candidate_id"], "snapshot_id": first["snapshot_id"],
                "decision_asof_ms": T, "comparability_hash": old["window"]["comparability_hash"]}]
    packet = packets.build_packet(later, history=history)
    assert packet["window"]["comparability_hash"] != old["window"]["comparability_hash"]
    assert "candidate_history_method_boundary" in packet["gaps"]
    assert packet["window"]["coverage"][0]["sample_count"] == 1


@pytest.mark.parametrize("clock", ["observation_end_ms", "first_seen_at_ms", "published_at_ms", "retrieved_at_ms"])
def test_future_source_is_rejected_even_with_valid_manifest_and_snapshot_hash(clock):
    snapshot, _ = snapshot_at()
    manifest = {"cutoff_at_ms": T, "candidate_id": snapshot["candidate_id"],
                "source_records": [{"record_id": "future", "time": {clock: T + 1}}]}
    manifest["manifest_hash"] = core._hash(manifest)
    snapshot["data_identity"]["source_manifest"] = manifest
    snapshot["snapshot_id"] = core._hash({k: snapshot[k] for k in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")})
    with pytest.raises(ValueError, match="future_source_clock"):
        packets.build_packet(snapshot)


def test_stale_quotes_do_not_renew_on_export():
    payload = sample(risk=False)
    payload["market"]["valuation_ts_ms"] = T + 1_000_000
    snapshot = core.evaluate(payload)
    packet = packets.build_packet(snapshot, raw_payload=payload)
    assert packet["snapshot"]["status"] == "expired_or_stale"
    assert packet["valuation"]["market"]["quote"]["exchange_times_ms"]["short"] == T
    assert packet["valuation"]["market"]["quote"]["credit_btc"] is None
    assert "short_book_stale_or_future" in packet["gaps"]


def test_restart_idempotency_sampling_and_method_boundary(tmp_path):
    archive = packets.CandidateResearchArchive(tmp_path)
    first, _ = snapshot_at()
    assert archive.sample(first, recorded_at_ms=T)["status"] == "recorded"
    persisted = (tmp_path / "index.jsonl").read_bytes()
    restarted = packets.CandidateResearchArchive(tmp_path)
    assert restarted.sample(first, recorded_at_ms=T)["reason"] == "snapshot_already_archived"
    assert (tmp_path / "index.jsonl").read_bytes() == persisted
    early, _ = snapshot_at(T + 1)
    assert restarted.sample(early, recorded_at_ms=T + 1)["reason"] == "sampling_interval_not_elapsed"
    later, _ = snapshot_at(T + 300_000)
    assert restarted.sample(later, recorded_at_ms=T + 300_000)["status"] == "recorded"
    assert restarted.replay(later["snapshot_id"])["window"]["coverage"][0]["sample_count"] == 2
    history = restarted.rows()
    history[0]["comparability_hash"] = "old-method"
    rebuilt = packets.build_packet(later, history=history)
    assert "candidate_history_method_boundary" in rebuilt["gaps"]
    assert rebuilt["window"]["coverage"][0]["sample_count"] == 1


def test_candidate_rotation_quantity_identity_and_no_cross_candidate_history(tmp_path):
    archive = packets.CandidateResearchArchive(tmp_path)
    first, _ = snapshot_at()
    changed, _ = snapshot_at(quantity=1)
    assert first["candidate_id"] != changed["candidate_id"]
    archive.sample(first, recorded_at_ms=T)
    archive.sample(changed, recorded_at_ms=T)
    assert archive.rows()[-1]["rotated_from_candidate_id"] == first["candidate_id"]
    packet = archive.replay(changed["snapshot_id"])
    assert packet["window"]["coverage"][0]["sample_count"] == 1


@pytest.mark.parametrize("change", ["legs", "expiry"])
def test_changed_legs_or_expiry_never_share_candidate_identity(change):
    original, raw = snapshot_at()
    if change == "legs":
        for leg in ("short", "long"):
            strike = raw["metadata"][leg]["strike"] + 1000
            name = f"BTC-26SEP26-{strike}-P"
            raw["metadata"][leg].update(instrument_name=name, strike=strike)
            raw["candidate"][leg + "_instrument"] = name
            raw["market"][leg + "_book"]["instrument_name"] = name
    else:
        for leg in ("short", "long"):
            name = raw["candidate"][leg + "_instrument"].replace("26SEP26", "27SEP26")
            raw["candidate"][leg + "_instrument"] = name
            raw["metadata"][leg].update(instrument_name=name,
                expiration_timestamp=raw["metadata"][leg]["expiration_timestamp"] + 86_400_000)
            raw["market"][leg + "_book"]["instrument_name"] = name
    changed = core.evaluate(raw)
    assert changed["candidate_id"] != original["candidate_id"]
    assert packets.build_packet(changed)["window"]["coverage"][0]["sample_count"] == 1


def test_future_history_rejected_and_sparse_window_not_shrunk():
    snapshot, _ = snapshot_at()
    packet = packets.build_packet(snapshot)
    future = {"candidate_id": snapshot["candidate_id"], "snapshot_id": "future",
              "decision_asof_ms": T + 1, "comparability_hash": packet["window"]["comparability_hash"]}
    with pytest.raises(ValueError, match="future_candidate_history"):
        packets.build_packet(snapshot, history=[future])
    assert [w["requested_window_ms"] for w in packet["window"]["coverage"]] == [900_000, 3_600_000]
    assert all(w["status"] == "insufficient" for w in packet["window"]["coverage"])


@pytest.mark.parametrize("offsets,expected", [([0, 300_010, 600_020], "covered"),
                                              ([0, 300_010], "insufficient")])
def test_window_uses_preboundary_anchor_for_refresh_jitter_without_hiding_gaps(offsets, expected):
    current, _ = snapshot_at(T + 900_030)
    comparison = packets.build_packet(current)["window"]["comparability_hash"]
    history = []
    for offset in offsets:
        snapshot, _ = snapshot_at(T + offset)
        history.append({"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
                        "decision_asof_ms": T + offset, "comparability_hash": comparison})
    window = packets.build_packet(current, history=history)["window"]["coverage"][0]
    assert window["status"] == expected
    assert window["requested_window_ms"] == 900_000
    assert window["actual_coverage_ms"] == 900_030
    assert window["boundary_anchor"] == {"snapshot_id": history[0]["snapshot_id"], "decision_asof_ms": T}
    assert window["sample_count"] == len(offsets) + 1


def test_byte_budget_and_sample_cap_stop_without_deleting_evidence(tmp_path):
    snapshot, _ = snapshot_at()
    archive = packets.CandidateResearchArchive(tmp_path, max_bytes=1)
    assert archive.sample(snapshot, recorded_at_ms=T)["reason"] == "research_byte_budget_exhausted"
    assert not list(tmp_path.glob("*.json"))
    archive = packets.CandidateResearchArchive(tmp_path, max_samples=1)
    archive.sample(snapshot, recorded_at_ms=T)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    later, _ = snapshot_at(T + 300_000)
    assert archive.sample(later, recorded_at_ms=T + 300_000)["reason"] == "research_sample_budget_exhausted"
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_native_cache_subdirectory_counts_toward_archive_byte_budget(tmp_path):
    snapshot, _ = snapshot_at()
    cache = tmp_path/'native_sources'
    cache.mkdir()
    cached = cache/'source.json'
    cached.write_bytes(b'x'*4096)
    archive = packets.CandidateResearchArchive(tmp_path,max_bytes=4096)
    assert archive.sample(snapshot,recorded_at_ms=T)['reason']=='research_byte_budget_exhausted'
    assert cached.read_bytes()==b'x'*4096 and archive.rows()==[]


def test_producer_state_signature_is_decoded_but_credentials_stay_rejected():
    state={'state':'READY','since_ts_ms':T,'previous_state':'OBSERVE','last_transition_ts_ms':T,
           'reason_codes':[],'anchor_ok':True,'net_gamma_ok':True,'net_gamma_sign':'POSITIVE',
           'net_gamma_transition':None,'net_gamma_conflict':False}
    health={'last_exported_spacegate_signature':json.dumps(state)}
    packets._assert_packet_safe_value({'snapshot':{'fact_context':{'export_health':health}}})
    for bad in ({'api_key':'secret'}, {**state,'api_key':'secret'}):
        with pytest.raises(ValueError,match='signature_schema'):
            packets._assert_packet_safe_value({'export_health':{'spacegate_signature':json.dumps(bad)}})
    with pytest.raises(packets.data_engine.DataEngineError,match='secret-like field'):
        packets._assert_packet_safe_value({'export_health':{**health,'api_key':'secret'}})
    with pytest.raises(packets.data_engine.DataEngineError,match='secret-like field'):
        packets._assert_packet_safe_value({'authorization_signature':'anything'})


def test_concurrent_retries_only_append_once_and_partial_index_fails_closed(tmp_path):
    snapshot, _ = snapshot_at()
    archive = packets.CandidateResearchArchive(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: archive.sample(snapshot, recorded_at_ms=T), range(2)))
    assert [r["status"] for r in results].count("recorded") == 1
    assert len(archive.rows()) == 1
    with archive.index_path.open("ab") as handle:
        handle.write(b'{"partial":')
    with pytest.raises(ValueError, match="not valid JSON"):
        archive.sample(snapshot, recorded_at_ms=T)


def test_optin_refresh_archives_exact_view_get_is_readonly_and_primary_ledger_unchanged(tmp_path):
    wb, fmz_path = synthetic_workbench(tmp_path)
    fx.write_fmz(fmz_path, fx.native_gate())
    wb.config.research_archive_enabled = True
    wb.research_archive = packets.CandidateResearchArchive(tmp_path / "research")
    view = wb.refresh(now_ms=T, record_daily=False)
    assert len(wb.research_archive.rows()) == 2
    assert not wb.config.ledger_path.exists()
    before = wb.research_archive.index_path.read_bytes()
    wb.cached_view()
    assert wb.research_archive.index_path.read_bytes() == before
    for candidate in view["candidates"]:
        packet = wb.research_archive.replay(candidate["snapshot"]["snapshot_id"])
        assert packet["provenance"]["source_kind"] == "synthetic_fixture"
        assert packet["snapshot"] == candidate["snapshot"]
        assert packet["replay"]["raw_quote_status"] == "raw_leg_books_replayed"
        assert packet["sources"]["clock_gaps"]
        assert packet["sources"]["manifest_clock_gaps"]
        assert "original_source_acquisition_clocks_not_frozen" in packet["gaps"]
        receipt = next(row for row in packet["sources"]["acquisition_receipts"].values()
                       if row["time"].get("retrieved_at_ms") is not None)
        assert receipt["time"]["retrieved_at_ms"] <= T
        assert receipt["time"]["first_seen_at_ms"] <= T
        assert candidate["review_gate"]["enabled"] is False
    assert view["llm_call_count"] == 0


def test_matching_source_receipts_reject_future_or_replaced_first_seen(tmp_path):
    wb, fmz_path = synthetic_workbench(tmp_path)
    fx.write_fmz(fmz_path, fx.native_gate())
    snapshot = wb.refresh(now_ms=T, record_daily=False)["candidates"][0]["snapshot"]
    receipts = packets.collect_source_receipts(snapshot, wb.data_engine)
    packet = packets.build_packet(snapshot, source_receipts=receipts)
    assert packets.replay_packet(packet)["status"] == "PASS"
    receipt = next(iter(receipts.values()))
    receipt["time"]["retrieved_at_ms"] = T + 1
    with pytest.raises(ValueError, match="future_source_receipt"):
        packets.build_packet(snapshot, source_receipts=receipts)
    receipt["time"]["retrieved_at_ms"] = None
    receipt["time"]["first_seen_at_ms"] -= 1
    with pytest.raises(ValueError, match="original_clock_mismatch"):
        packets.build_packet(snapshot, source_receipts=receipts)


def test_archive_error_does_not_erase_quote_or_change_review_gate(tmp_path, monkeypatch):
    wb, fmz_path = synthetic_workbench(tmp_path)
    fx.write_fmz(fmz_path, fx.native_gate())
    wb.config.research_archive_enabled = True
    wb.research_archive = packets.CandidateResearchArchive(tmp_path / "research")
    def failed(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(wb.research_archive, "sample", failed)
    view = wb.refresh(now_ms=T, record_daily=False)
    assert all(c["status"] == "snapshot" for c in view["candidates"])
    assert all(c["research_archive"]["status"] == "error" for c in view["candidates"])
    assert all(c["review_gate"]["enabled"] is False for c in view["candidates"])
    assert view["llm_call_count"] == 0


def test_disabled_by_default_and_env_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.delenv("ASTRA_RESEARCH_ARCHIVE_ENABLED", raising=False)
    assert fx.workbench.config_from_env().research_archive_enabled is False
    wb, fmz_path = synthetic_workbench(tmp_path)
    fx.write_fmz(fmz_path, fx.native_gate())
    wb.refresh(now_ms=T, record_daily=False)
    assert wb.research_archive is None
    assert not (wb.config.state_dir / "candidate_research").exists()


def test_bad_model_catalog_does_not_block_basic_archive(tmp_path):
    original, fmz_path = synthetic_workbench(tmp_path)
    fx.write_fmz(fmz_path, fx.native_gate())
    original.config.research_archive_enabled = True
    original.config.risk_model_path = tmp_path / "missing_model.json"
    original.config.risk_protocol_path = tmp_path / "missing_protocol.json"
    wb = fx.workbench.AstraWorkbench(original.config, discovery_capture=fx.fake_discovery,
        quote_capture=synthetic_quote_capture, clock_ms=lambda: T)
    view = wb.refresh(now_ms=T, record_daily=False)
    assert view["research_archive"]["feature_catalog_error_type"] == "FileNotFoundError"
    assert len(wb.research_archive.rows()) == 2
    packet = wb.research_archive.replay(view["candidates"][0]["snapshot"]["snapshot_id"])
    assert packet["model_input"]["status"] == "risk_asset_unavailable"
    assert "risk_asset_export_failed:FileNotFoundError" in packet["gaps"]


def test_code_release_does_not_break_window_comparability(monkeypatch):
    # P0: a code deploy that keeps replay semantics must not restart the window.
    snapshot, _raw = snapshot_at(T)
    before = packets.build_packet(snapshot)["window"]["comparability_hash"]
    real_read_bytes = packets.Path.read_bytes

    def edited_source(self):
        data = real_read_bytes(self)
        return data + b"\n# comment-only edit\n" if self.name == "astra_underwriting_v20.py" else data

    monkeypatch.setattr(packets.Path, "read_bytes", edited_source)
    after = packets.build_packet(snapshot)
    assert after["window"]["comparability_hash"] == before
    assert after["versions"]["replay_validator_code_hashes"]  # still kept for audit
