from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import hashlib

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_underwriting_v20 as core
from tools import astra_wpem_results_v1 as results

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "wpem_rc"
ROOT = FIXTURE_ROOT / "wpem_round2"
RAW_DIR = ROOT / "official_raw"
EVIDENCE = ROOT / "evidence"
RISK = ROOT / "risk_qualification"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def raw_sources() -> dict[str, bytes]:
    return {
        "official_raw/delivery_prices.json": read_bytes(RAW_DIR / "delivery_prices.json"),
        "official_raw/delivery_events.json": read_bytes(RAW_DIR / "delivery_events.json"),
        "official_raw/BTC-26SEP26-84000-P.json": read_bytes(RAW_DIR / "BTC-26SEP26-84000-P.json"),
        "official_raw/BTC-26SEP26-82000-P.json": read_bytes(RAW_DIR / "BTC-26SEP26-82000-P.json"),
        "official_raw/BTC-26SEP26-84500-C.json": read_bytes(RAW_DIR / "BTC-26SEP26-84500-C.json"),
        "official_raw/BTC-26SEP26-86500-C.json": read_bytes(RAW_DIR / "BTC-26SEP26-86500-C.json"),
    }


def receipts():
    return read_json(ROOT / "official_capture_receipts.json")


def packet(side: str):
    return read_json(EVIDENCE / f"historical_real_{side}_packet.json")


def qualification(side: str):
    return read_json(RISK / side / "risk_qualification_report.json")


def attach(side: str):
    return results.result_attachment(packet(side), raw_sources(), receipts(), qualification(side))


def blocked_qualification_for(pkt: dict) -> dict:
    body = {
        "schema": "wpem_reference_qualification@1.0.0-shadow",
        "shadow_only": True,
        "terminal_status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "earliest_breakpoint": "TEST_BLOCKED",
        "status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "packet_hash": pkt["packet_hash"],
        "catalog_hash": "c" * 64,
        "input_row_hash": "i" * 64,
        "asset_sha256": "a" * 64,
        "protocol_sha256": "p" * 64,
        "candidate_id": pkt["identity"]["candidate_id"],
        "snapshot_id": pkt["identity"]["snapshot_id"],
        "decision_asof_ms": pkt["cutoff"]["decision_asof_ms"],
        "model_id": "test-blocked",
        "model_hash": "m" * 64,
        "feature_count": 0,
        "required_feature_count": 10,
        "required_missing_count": 10,
        "required_missing_features": ["fee_or_quote_fixture"],
        "required_coverage": {"available": 0, "total": 10, "missing": 10},
        "feature_binding_manifest_hash": "f" * 64,
        "target_price_basis_binding_hash": "t" * 64,
        "gap_reasons": ["TEST_BLOCKED"],
        "shadow_inference_allowed": False,
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
        "row_qualified": False,
        "production_risk_admitted": False,
        "execution_allowed": False,
        "inference_calls": 0,
        "llm_http_calls": 0,
    }
    return {**body, "qualification_report_hash": results._hash(body)}


def rebuilt_packet_from_historical(side: str, mutator) -> dict:
    raw_payload = deepcopy(packet(side)["raw_payload"])
    mutator(raw_payload)
    snapshot = core.evaluate(raw_payload)
    return packets.build_packet(snapshot, raw_payload=raw_payload)


def _replace_raw(raws: dict[str, bytes], rec: dict, key: str, value: dict):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raws[key] = payload
    sha = hashlib.sha256(payload).hexdigest()
    for row in rec["requests"]:
        if row["path"] == key:
            row["sha256"] = sha
            row["bytes"] = len(payload)


def test_put_result_attachment_closes_official_liability_without_actual_trade():
    attachment = attach("put")
    assert attachment["result_identity"] == "RECONSTRUCTED_OFFICIAL_RESULT"
    assert attachment["official_result"]["delivery_price"] == pytest.approx(84042.83)
    assert attachment["official_result"]["delivery_event"]["timestamp"] == 1790409600086
    assert attachment["accounting"]["L_official_btc"] == pytest.approx(0.0)
    assert attachment["accounting"]["c_visible_btc"] == pytest.approx(0.0035000000000000005)
    assert attachment["accounting"]["fee_btc"] == pytest.approx(0.0004125)
    assert attachment["accounting"]["Pi_quote_hold_btc"] == pytest.approx(0.0030875000000000004)
    assert attachment["accounting"]["payout_occurred"] is False
    assert attachment["accounting"]["quote_hold_net_loss"] is False
    assert attachment["accounting"]["Pi_actual_btc"] is None
    assert attachment["accounting"]["actual_net_loss_btc"] is None
    assert attachment["accounting"]["actual_net_loss_status"] == "UNKNOWN_NOT_RECORDED"
    assert attachment["accounting"]["decision_status"] == "UNKNOWN_NOT_RECORDED"
    assert attachment["risk_reference"]["mu_ref_btc"] is None
    assert attachment["risk_reference"]["M_ref_btc"] is None
    assert attachment["risk_reference"]["error_btc"] is None
    assert attachment["validation_counts"]["raw_sources"] == 6
    assert attachment["result_attachment_hash"] == results._hash(
        {k: v for k, v in attachment.items() if k != "result_attachment_hash"}
    )


def test_call_result_attachment_closes_official_liability_without_actual_trade():
    attachment = attach("call")
    assert attachment["contract"]["short_instrument"] == "BTC-26SEP26-84500-C"
    assert attachment["accounting"]["L_official_btc"] == pytest.approx(0.0)
    assert attachment["accounting"]["c_visible_btc"] == pytest.approx(0.004399999999999999)
    assert attachment["accounting"]["fee_btc"] == pytest.approx(0.00043749999999999995)
    assert attachment["accounting"]["Pi_quote_hold_btc"] == pytest.approx(0.003962499999999999)
    assert attachment["accounting"]["payout_occurred"] is False
    assert attachment["accounting"]["quote_hold_net_loss"] is False
    assert attachment["official_result"]["official_rule"]["rule_version"] == (
        "deribit_inverse_options_settlement_change_2026-08-01"
    )


def test_unknown_fee_stays_unknown_and_does_not_use_receipt_time():
    def mutate(raw_payload):
        raw_payload["fee"] = {"basis": "unknown"}

    pkt = rebuilt_packet_from_historical("put", mutate)
    attachment = results.result_attachment(pkt, raw_sources(), receipts(), blocked_qualification_for(pkt))
    assert attachment["accounting"]["fee_btc"] is None
    assert attachment["accounting"]["c_visible_btc"] == pytest.approx(0.0035000000000000005)
    assert attachment["accounting"]["Pi_quote_hold_btc"] is None
    assert attachment["accounting"]["quote_hold_net_loss"] is None
    assert attachment["decision_asof_ms"] == pkt["cutoff"]["decision_asof_ms"]
    assert sorted(attachment["gaps"]) == ["fee_unknown"]


def test_incomplete_fee_coverage_keeps_quote_hold_unknown_even_with_finite_partial_fee():
    def mutate(raw_payload):
        raw_payload["fee"]["covers"] = ["entry"]

    pkt = rebuilt_packet_from_historical("put", mutate)
    assert pkt["valuation"]["fee"]["amount_btc"] is None
    assert "fee_coverage_incomplete" in pkt["valuation"]["fee"]["gap_reasons"]
    attachment = results.result_attachment(pkt, raw_sources(), receipts(), blocked_qualification_for(pkt))
    assert attachment["accounting"]["fee_btc"] is None
    assert attachment["accounting"]["Pi_quote_hold_btc"] is None
    assert attachment["accounting"]["quote_hold_net_loss"] is None
    assert "fee_unknown" in attachment["gaps"]


def test_unknown_quote_stays_unknown_and_does_not_use_receipt_time():
    def mutate(raw_payload):
        raw_payload["market"]["short_book"]["timestamp"] = raw_payload["market"]["valuation_ts_ms"] - 10_000

    pkt = rebuilt_packet_from_historical("put", mutate)
    assert pkt["valuation"]["market"]["quote"]["credit_btc"] is None
    attachment = results.result_attachment(pkt, raw_sources(), receipts(), blocked_qualification_for(pkt))
    assert attachment["accounting"]["c_visible_btc"] is None
    assert attachment["accounting"]["Pi_quote_hold_btc"] is None
    assert attachment["accounting"]["quote_hold_net_loss"] is None
    assert "visible_credit_unknown" in attachment["gaps"]


def test_packet_identity_or_financial_tamper_without_new_hash_is_rejected():
    tampered = packet("put")
    tampered["identity"]["contract"]["quantity_btc"] = 0.5
    with pytest.raises(ValueError, match="hash_mismatch|valuation_mismatch|replay_"):
        results.result_attachment(tampered, raw_sources(), receipts(), qualification("put"))
    tampered = packet("put")
    tampered["valuation"]["fee"]["amount_btc"] = 0.0
    with pytest.raises(ValueError, match="hash_mismatch|valuation_mismatch|replay_"):
        results.result_attachment(tampered, raw_sources(), receipts(), qualification("put"))
    tampered = packet("put")
    tampered["valuation"]["economics"]["visible_credit_btc"] = 99.0
    with pytest.raises(ValueError, match="hash_mismatch|valuation_mismatch|replay_"):
        results.result_attachment(tampered, raw_sources(), receipts(), qualification("put"))


def test_event_price_must_match_delivery_price_row():
    raws = raw_sources()
    rec = receipts()
    events = json.loads(raws["official_raw/delivery_events.json"].decode("utf-8"))
    match = next(row for row in events["result"]["settlements"] if row.get("instrument_name") == "BTC-26SEP26")
    match["index_price"] = 84042.84
    _replace_raw(raws, rec, "official_raw/delivery_events.json", events)
    with pytest.raises(ValueError, match="delivery_event_index_price_mismatch"):
        results.result_attachment(packet("put"), raws, rec, qualification("put"))


def test_raw_tamper_without_receipt_update_is_rejected():
    raws = raw_sources()
    prices = json.loads(raws["official_raw/delivery_prices.json"].decode("utf-8"))
    next(row for row in prices["result"]["data"] if row["date"] == "2026-09-26")["delivery_price"] = 1.0
    raws["official_raw/delivery_prices.json"] = json.dumps(prices).encode("utf-8")
    with pytest.raises(ValueError, match="official_raw_sha256_mismatch"):
        results.result_attachment(packet("put"), raws, receipts(), qualification("put"))


def test_wrong_instrument_expiry_is_rejected_even_with_updated_receipt_hash():
    raws = raw_sources()
    rec = receipts()
    meta = json.loads(raws["official_raw/BTC-26SEP26-84000-P.json"].decode("utf-8"))
    meta["result"]["expiration_timestamp"] += 86_400_000
    _replace_raw(raws, rec, "official_raw/BTC-26SEP26-84000-P.json", meta)
    with pytest.raises(ValueError, match="instrument_expiry_mismatch"):
        results.result_attachment(packet("put"), raws, rec, qualification("put"))


def test_quantity_must_be_positive():
    pkt = packet("put")
    pkt["identity"]["contract"]["quantity_btc"] = 0
    with pytest.raises(ValueError, match="packet_hash_mismatch"):
        results.result_attachment(pkt, raw_sources(), receipts(), qualification("put"))


def test_tiny_delivery_price_keeps_inverse_tail_above_quote_budget_without_clipping():
    raws = raw_sources()
    rec = receipts()
    prices = json.loads(raws["official_raw/delivery_prices.json"].decode("utf-8"))
    next(row for row in prices["result"]["data"] if row["date"] == "2026-09-26")["delivery_price"] = 100.0
    events = json.loads(raws["official_raw/delivery_events.json"].decode("utf-8"))
    match = next(row for row in events["result"]["settlements"] if row.get("instrument_name") == "BTC-26SEP26")
    match["index_price"] = 100.0
    match["mark_price"] = 100.0
    _replace_raw(raws, rec, "official_raw/delivery_prices.json", prices)
    _replace_raw(raws, rec, "official_raw/delivery_events.json", events)
    attachment = results.result_attachment(packet("put"), raws, rec, qualification("put"))
    assert attachment["accounting"]["L_official_btc"] == pytest.approx(20.0)
    assert attachment["accounting"]["payout_occurred"] is True
    assert attachment["accounting"]["L_official_btc"] / attachment["accounting"]["c_visible_btc"] > 1
    assert attachment["accounting"]["Pi_quote_hold_btc"] < 0
    assert attachment["accounting"]["quote_hold_net_loss"] is True


def test_reconstructed_result_never_becomes_actual_trade_evidence():
    rec = receipts()
    rec["actual_trade_evidence"] = True
    with pytest.raises(ValueError, match="official_receipts_actual_trade_evidence_present"):
        results.result_attachment(packet("put"), raw_sources(), rec, qualification("put"))
    attachment = attach("put")
    assert attachment["accounting"]["actual_trade_status"] == "UNKNOWN_NOT_RECORDED"
    assert attachment["accounting"]["actual_net_loss_status"] == "UNKNOWN_NOT_RECORDED"
    assert attachment["accounting"]["quote_hold_is_actual_trade"] is False


def test_expiry_must_be_mature_before_official_result_time():
    rec = receipts()
    for row in rec["requests"]:
        row["request_started_ms"] = 1790409599000
        row["retrieved_at_ms"] = 1790409599999
    with pytest.raises(ValueError, match="official_result_acquired_before_expiry"):
        results.result_attachment(packet("put"), raw_sources(), rec, qualification("put"))


def test_receipt_time_order_and_duplicate_conflict_are_rejected():
    rec = receipts()
    rec["requests"][0]["request_started_ms"] = rec["requests"][0]["retrieved_at_ms"] + 1
    with pytest.raises(ValueError, match="official_receipt_client_time_invalid"):
        results.result_attachment(packet("put"), raw_sources(), rec, qualification("put"))
    rec = receipts()
    rec["requests"][0]["server_us_in"] = rec["requests"][0]["server_us_out"] + 1
    with pytest.raises(ValueError, match="official_receipt_server_time_order_invalid"):
        results.result_attachment(packet("put"), raw_sources(), rec, qualification("put"))
    rec = receipts()
    dup = deepcopy(rec["requests"][0])
    dup["sha256"] = "0" * 64
    rec["requests"].append(dup)
    with pytest.raises(ValueError, match="official_receipt_duplicate_conflict"):
        results.result_attachment(packet("put"), raw_sources(), rec, qualification("put"))


def test_delivery_event_timestamp_must_be_near_expiry():
    raws = raw_sources()
    rec = receipts()
    events = json.loads(raws["official_raw/delivery_events.json"].decode("utf-8"))
    match = next(row for row in events["result"]["settlements"] if row.get("instrument_name") == "BTC-26SEP26")
    match["timestamp"] = 1790409599999
    _replace_raw(raws, rec, "official_raw/delivery_events.json", events)
    with pytest.raises(ValueError, match="delivery_event_timestamp_not_near_expiry"):
        results.result_attachment(packet("put"), raws, rec, qualification("put"))


def test_fake_or_mismatched_qualification_is_rejected():
    fake = deepcopy(qualification("put"))
    fake["status"] = "QUALIFIED_SHADOW_REFERENCE_ONLY"
    fake["qualification_report_hash"] = results._hash(
        {k: v for k, v in fake.items() if k != "qualification_report_hash"}
    )
    with pytest.raises(ValueError, match="reference_qualification_not_terminal_blocked"):
        results.result_attachment(packet("put"), raw_sources(), receipts(), fake)
    fake = deepcopy(qualification("put"))
    fake["packet_hash"] = "0" * 64
    fake["qualification_report_hash"] = results._hash(
        {k: v for k, v in fake.items() if k != "qualification_report_hash"}
    )
    with pytest.raises(ValueError, match="reference_qualification_packet_mismatch"):
        results.result_attachment(packet("put"), raw_sources(), receipts(), fake)


def test_synthetic_pure_accounting_fixture_payout_can_occur_without_quote_hold_net_loss():
    # Synthetic unit fixture only: this is not an official maturity result case.
    account, gaps = results._accounting(packet("put"), 0.0001)
    assert gaps == []
    assert account["payout_occurred"] is True
    assert account["Pi_quote_hold_btc"] == pytest.approx(0.0029875000000000003)
    assert account["quote_hold_net_loss"] is False
    assert account["Pi_actual_btc"] is None
    assert account["actual_net_loss_btc"] is None
    assert account["actual_net_loss_status"] == "UNKNOWN_NOT_RECORDED"
