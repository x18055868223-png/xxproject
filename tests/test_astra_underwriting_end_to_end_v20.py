from pathlib import Path
import json
import math

import pytest

from tools import astra_underwriting_card_bridge_v20 as bridge
from tools import astra_underwriting_real_replay_v20 as replay
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_review_v20 as review
from test_astra_underwriting_review_v20 import Budget, current_payload, good_answer


SOURCE = ".artifacts/astra-credit-space-20260914/snapshot/snapshot.json"


@pytest.mark.skipif(
    not any((base / SOURCE).is_file() for base in (Path.cwd(), *Path.cwd().parents)),
    reason="saved real quote capture is not present in this checkout",
)
def test_real_saved_quote_replay_remains_openable_as_historical_json(tmp_path: Path):
    payload = replay.build_payload(SOURCE, "put_0", 0.1)
    annex = store.UnderwritingLedger(tmp_path / "ledger.jsonl").record_payload(payload)
    card = bridge.annex_to_card(annex)
    snapshot = card["underwriting_snapshot"]
    assert card["display_layers"]["headline"].endswith("历史回放")
    assert snapshot["contract"]["short_instrument"] == "BTC-15SEP26-77500-P"
    assert snapshot["contract"]["long_instrument"] == "BTC-15SEP26-75500-P"
    assert snapshot["economics"]["visible_credit_btc"] == 0.0004
    assert snapshot["fee"]["amount_btc"] == 0.0000375
    assert snapshot["economics"]["loss_budget_btc"] == 0.00036250000000000003
    assert snapshot["economics"]["mu_ref_btc"] is None
    assert snapshot["economics"]["reference_margin_btc"] is None
    assert snapshot["data_identity"]["kind"] == "historical_replay"
    assert card["execution_allowed"] is False


def test_one_fake_transport_review_stays_bound_to_original_json_and_account(tmp_path: Path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    annex = ledger.record_payload(current_payload())
    snapshot = annex["snapshot"]
    economic_before = json.dumps(snapshot["economics"], sort_keys=True)
    answer = good_answer(snapshot)
    answer["selected_claim_ids"].append("claim.quote.visible_credit")
    answer["selected_reassess_condition_ids"] = ["reassess.risk_reference_available"]
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}]}

    result = review.run_review(snapshot, ledger, tmp_path / "state", api_key="fake", model="fake",
                               budget=Budget(), transport=transport)
    assert result["status"] == "OK"
    assert len(calls) == 1
    card = bridge.annex_to_card(store.build_card_annex(ledger.state(), snapshot["snapshot_id"]))
    assert json.dumps(card["underwriting_snapshot"]["economics"], sort_keys=True) == economic_before
    attached = card["underwriting_review"]
    assert attached["status"] == "OK"
    assert attached["reviewed_snapshot_id"] == snapshot["snapshot_id"]
    assert "未成交的顺序分腿估值" in json.dumps(attached, ensure_ascii=False)
    assert "取得绑定同一候选、同一估值时点和支持域的风险参考后重新核对" in json.dumps(attached, ensure_ascii=False)
    assert attached["http_calls"] == 1
    assert math.isclose(card["underwriting_snapshot"]["economics"]["visible_credit_btc"], 0.0004, abs_tol=1e-12)
    assert card["execution_allowed"] is False
