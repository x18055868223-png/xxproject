import json
from pathlib import Path

import pytest

from tools import astra_underwriting_real_replay_v20 as replay
from tools import astra_underwriting_v20 as core


SOURCE = ".artifacts/astra-credit-space-20260914/snapshot/snapshot.json"
pytestmark = pytest.mark.skipif(
    not any((base / SOURCE).is_file() for base in (Path.cwd(), *Path.cwd().parents)),
    reason="saved real quote capture is not present in this checkout",
)


def source_path() -> Path:
    return replay.resolve_snapshot_path(SOURCE)


def test_real_put0_replay_restores_credit_fee_and_keeps_mu_missing() -> None:
    payload = replay.build_payload(SOURCE, "put_0", 0.1)
    snapshot = core.evaluate(payload)

    assert payload["schema"] == replay.SCHEMA
    assert payload["candidate"]["source_candidate_id"] == "put_0"
    assert payload["fee"]["basis"] == "configured_assumption"
    assert payload["fee"]["assumption_basis"] == "configured_assumption"
    assert payload["fee"]["covers"] == ["entry", "delivery"]
    assert payload["fee"]["entry_fee_btc"] == pytest.approx(0.0000375)
    assert payload["fee"]["delivery_fee_btc"] == 0.0
    assert snapshot["market"]["quote"]["credit_btc"] == pytest.approx(0.0004)
    assert snapshot["fee"]["amount_btc"] == pytest.approx(0.0000375)
    assert snapshot["economics"]["loss_budget_btc"] == pytest.approx(0.0003625)
    assert snapshot["economics"]["mu_ref_btc"] is None
    assert snapshot["economics"]["minimum_reference_credit_btc"] is None
    assert snapshot["economics"]["reference_margin_btc"] is None
    assert snapshot["status"] == "information_gap"
    assert "frozen_risk_reference_missing" in snapshot["gap_reasons"]
    assert "historical_replay_does_not_fabricate_fmz_fact_context" in snapshot["gap_reasons"]


def test_real_fee_arithmetic_uses_deribit_cap_per_entry_leg_and_zero_daily_delivery() -> None:
    payload = replay.build_payload(SOURCE, "put_0", 0.1)
    detail = payload["replay_identity"]["fee_detail"]

    short_fill = detail["legs"]["short"]["fills"][0]
    long_fill = detail["legs"]["long"]["fills"][0]
    assert short_fill["price_btc"] == pytest.approx(0.0046)
    assert short_fill["fee_rate_btc"] == pytest.approx(0.0003)
    assert detail["legs"]["short"]["fee_btc"] == pytest.approx(0.00003)
    assert long_fill["price_btc"] == pytest.approx(0.0006)
    assert long_fill["fee_rate_btc"] == pytest.approx(0.000075)
    assert detail["legs"]["long"]["fee_btc"] == pytest.approx(0.0000075)
    assert detail["delivery_fee_btc"] == 0.0
    assert detail["delivery_fee_rule"] == "daily_option_expiry_delivery_fee_zero_explicit"


def test_unknown_fee_keeps_real_credit_but_marks_economics_gap() -> None:
    payload = replay.build_payload(SOURCE, "put_0", 0.1, fee_basis="unknown")
    snapshot = core.evaluate(payload)

    assert payload["fee"] == {"basis": "unknown"}
    assert snapshot["market"]["quote"]["credit_btc"] == pytest.approx(0.0004)
    assert snapshot["economics"]["loss_budget_btc"] is None
    assert "fee_unknown" in snapshot["gap_reasons"]


def test_replay_identity_is_historical_and_does_not_modify_old_source(tmp_path: Path, capsys) -> None:
    source = source_path()
    before = replay.sha256_file(source)
    output = tmp_path / "payload.json"

    assert replay.main([
        "--snapshot",
        SOURCE,
        "--candidate-id",
        "put_0",
        "--quantity-btc",
        "0.1",
        "--output",
        str(output),
    ]) == 0
    capsys.readouterr()

    after = replay.sha256_file(source)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert after == before
    assert payload["replay_identity"]["source_kind"] == "frozen_real_quote_snapshot"
    assert payload["replay_identity"]["source_snapshot_sha256"] == before
    assert payload["replay_identity"]["source_candidate_id"] == "put_0"
    assert "no_network_request" in payload["replay_identity"]["limits"]
    assert "no_mu_reference_fabrication" in payload["replay_identity"]["limits"]


def test_missing_candidate_fails_without_substituting_another_structure() -> None:
    with pytest.raises(KeyError, match="candidate_id not found"):
        replay.build_payload(SOURCE, "put_99", 0.1)
