import copy
import json
from pathlib import Path

import pytest

from tools import astra_underwriting_card_bridge_v20 as bridge
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_review_v20 as review_core
from tools import astra_underwriting_v20 as core
from test_astra_underwriting_v20 import sample
from test_astra_underwriting_review_v20 import current_payload, good_answer


def test_store_annex_becomes_standalone_card_without_rewriting_existing_cards(tmp_path: Path):
    site = tmp_path / "site"
    cards = site / "signal_cards"
    cards.mkdir(parents=True)
    old_card = cards / "old-card.json"
    old_bytes = b'{"identity":{"card_id":"old-card"},"llm_review":{"status":"OLD"}}\n'
    old_card.write_bytes(old_bytes)
    index = {"schema": {"name": "signal_cards_manifest", "version": "1.0.0"},
             "cards": [{"card_id": "old-card", "path": "signal_cards/old-card.json"}]}
    store.write_json(cards / "index.json", index)

    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    annex = ledger.record_payload(sample())
    target = bridge.export_to_site(annex, site)
    card = json.loads(target.read_text(encoding="utf-8"))
    assert card["underwriting_snapshot"]["snapshot_id"] == annex["snapshot"]["snapshot_id"]
    assert card["underwriting_review"] is None
    assert card["identity"]["is_synthetic"] is True
    assert card["execution_allowed"] is False
    assert old_card.read_bytes() == old_bytes
    assert len(store.read_json(cards / "index.json")["cards"]) == 2
    assert bridge.export_to_site(annex, site) == target
    assert len(store.read_json(cards / "index.json")["cards"]) == 2


def test_only_same_packet_validated_review_is_exported(tmp_path: Path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    annex = ledger.record_payload(current_payload())
    snapshot = annex["snapshot"]
    review = {**review_core.validate_wire_response(snapshot, good_answer(snapshot)), "http_calls": 1}
    annex = ledger.attach_review(review, snapshot_id=snapshot["snapshot_id"])
    card = bridge.annex_to_card(annex)
    assert card["underwriting_review"]["reviewed_snapshot_id"] == snapshot["snapshot_id"]
    assert card["underwriting_review"]["response_protocol"] == review_core.RESPONSE_PROTOCOL
    assert "确定性责任账" in card["underwriting_review"]["summary_cn"]
    broken = copy.deepcopy(annex)
    broken["review_annex"]["review"]["reviewed_snapshot_id"] = "another-snapshot"
    with pytest.raises(ValueError, match="does not match"):
        bridge.annex_to_card(broken)
    tampered = copy.deepcopy(annex)
    tampered["review_annex"]["review"]["summary_cn"] = "被篡改的本地摘要。"
    tampered["review_annex"]["review_hash"] = store._hash(tampered["review_annex"]["review"])
    with pytest.raises(ValueError, match="normalized fields"):
        bridge.annex_to_card(tampered)


def test_failed_attempt_is_bound_and_never_turned_into_review():
    annex = store.transient_card_annex(core.evaluate(current_payload()))
    snapshot = annex["snapshot"]
    annex["review_attempt"] = {
        "schema": "astra_underwriting_review_attempt_summary@2.0.0",
        "snapshot_id": snapshot["snapshot_id"], "candidate_id": snapshot["candidate_id"],
        "status": "rejected", "http_calls": 2,
    }
    card = bridge.annex_to_card(annex)
    assert card["underwriting_review"] is None
    assert card["underwriting_review_attempt"]["status"] == "rejected"
    annex["review_attempt"]["snapshot_id"] = "wrong"
    with pytest.raises(ValueError, match="attempt does not match"):
        bridge.annex_to_card(annex)


def test_stale_protocol_attempt_is_bound_and_exported_without_review():
    annex = store.transient_card_annex(core.evaluate(current_payload()))
    snapshot = annex["snapshot"]
    annex["review_attempt"] = {
        "schema": "astra_underwriting_review_attempt_summary@2.0.0",
        "snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "status": "stale_protocol_review",
        "http_calls": 1,
    }

    card = bridge.annex_to_card(annex)

    assert card["underwriting_review"] is None
    assert card["underwriting_review_attempt"]["status"] == "stale_protocol_review"


def test_legacy_attached_ok_review_is_readable_but_marked_stale_protocol(tmp_path: Path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    annex = ledger.record_payload(current_payload())
    snapshot = annex["snapshot"]
    packet = core.llm_applicability_packet(snapshot)
    old_review = {
        "schema": review_core.SCHEMA,
        "status": "OK",
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "packet_hash": core._hash(packet),
        "applicability": "UNABLE_TO_JUDGE",
        "supports": [{"ref": "contract", "claim_cn": "旧协议自由文本仅保留可读。"}],
        "counters": [],
        "gaps_cn": ["旧协议缺口文本可读但不合格。"],
        "reassess_if_cn": [],
        "summary_cn": "旧协议 OK 不能冒充当前协议通过。",
        "http_calls": 1,
    }
    store.append_jsonl(ledger.path, [store._event(store.REVIEW_EVENT, {
        "snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "review_hash": store._hash(old_review),
        "review": old_review,
    }, now_ms=2)])
    annex = store.build_card_annex(ledger.state(), snapshot["snapshot_id"])

    card = bridge.annex_to_card(annex)

    assert card["underwriting_review"]["status"] == "stale_protocol_review"
    assert card["underwriting_review"]["legacy_status"] == "OK"
    assert card["underwriting_review"]["legacy_review"] is True
    assert card["underwriting_review"]["current_protocol_qualified"] is False
    assert card["underwriting_review"]["supports"] == old_review["supports"]
