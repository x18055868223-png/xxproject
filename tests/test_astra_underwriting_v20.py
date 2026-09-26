import copy
import hashlib

import pytest

from tools import astra_underwriting_v20 as u


T = 1_790_323_200_000  # 2026-09-25 08:00 UTC; named expiry is next day 08:00 UTC.
EXPIRY = T + 86_400_000


def sample(side="put", *, risk=True):
    short_strike, long_strike = (97000, 95000) if side == "put" else (103000, 105000)
    suffix = "P" if side == "put" else "C"
    short_name = f"BTC-26SEP26-{short_strike}-{suffix}"
    long_name = f"BTC-26SEP26-{long_strike}-{suffix}"

    def meta(name, strike):
        return {"instrument_name": name, "kind": "option", "base_currency": "BTC",
                "settlement_currency": "BTC", "quote_currency": "BTC", "counter_currency": "USD",
                "price_index": "btc_usd", "option_type": side, "state": "open",
                "expiration_timestamp": EXPIRY, "contract_size": 1, "strike": strike,
                "min_trade_amount": 0.1}

    payload = {"candidate": {"short_instrument": short_name,
                              "long_instrument": long_name, "quantity_btc": 0.1,
                              "exit_policy": "hold_to_expiry"},
               "metadata": {"short": meta(short_name, short_strike),
                            "long": meta(long_name, long_strike)},
               "market": {"valuation_ts_ms": T, "received_ts_ms": T,
                          "reference_price_usd": 100000, "reference_source": "synthetic_fixture",
                          "short_book": {"instrument_name": short_name, "state": "open",
                                         "timestamp": T, "bids": [[0.007, 0.1]],
                                         "asks": [[0.008, 0.1]]},
                          "long_book": {"instrument_name": long_name, "state": "open",
                                        "timestamp": T, "bids": [[0.002, 0.1]],
                                        "asks": [[0.003, 0.1]]}},
               "fee": {"amount_btc": 0.0000375, "basis": "configured_assumption",
                       "covers": ["entry", "delivery"]},
               "origins": ["manual"]}
    if risk:
        identity = u.candidate_identity(payload["candidate"], payload["metadata"], T)
        payload["risk"] = {"status": "supported", "support_verified": True,
                           "candidate_id": identity["candidate_id"], "unit": "BTC",
                           "model_id": "synthetic-test-only", "model_hash": hashlib.sha256(b"fixture").hexdigest(),
                           "calibration_id": "synthetic-test-only", "support_scope": "synthetic-fixture-only",
                           "source_kind": "synthetic_fixture", "input_ts_ms": T,
                           "feature_end_ts_ms": T, "reference_price_usd": 100000,
                           "input_row_hash": hashlib.sha256(b"synthetic-input").hexdigest(),
                           "normalization_basis": "native_btc_fixture_direct",
                           "training_cutoff": "synthetic-not-trained",
                           "label_known_cutoff": "synthetic-not-trained",
                           "quantity_btc": 0.1, "horizon_hours": 24, "mu_btc": 0.00025}
    return payload


def test_complete_put_contract_and_compensation_account():
    snap = u.evaluate(sample())
    assert snap["schema"] == u.SCHEMA
    assert snap["market"]["quote"]["credit_btc"] == pytest.approx(0.0004)
    assert snap["economics"]["loss_budget_btc"] == pytest.approx(0.0003625)
    assert snap["economics"]["minimum_reference_credit_btc"] == pytest.approx(0.0002875)
    assert snap["economics"]["reference_margin_btc"] == pytest.approx(0.0001125)
    assert snap["liability"]["breakeven_usd"] == pytest.approx([96649.64509], rel=1e-7)
    assert snap["liability"]["usd_intrinsic_cap"] == 200
    assert snap["liability"]["btc_contract_cap"] is None
    assert snap["liability"]["account_margin_btc"] is None
    assert snap["execution_allowed"] is False
    assert snap["status"] == "reviewable"


def test_premium_fee_net_credit_and_spot_usd_are_explicitly_distinct():
    payload = sample()
    payload["fee"].update(entry_fee_btc=0.00003, delivery_fee_btc=0.0000075)
    snap = u.evaluate(payload)
    econ = snap["economics"]
    assert econ["short_premium_btc"] == pytest.approx(0.0007)
    assert econ["long_premium_btc"] == pytest.approx(0.0003)
    assert econ["premium_spread_btc"] == pytest.approx(0.0004)
    assert econ["theoretical_two_leg_fee_btc"] == pytest.approx(0.00003)
    assert econ["net_credit_after_entry_fee_btc"] == pytest.approx(0.00037)
    assert econ["net_credit_after_fee_btc"] == pytest.approx(0.0003625)
    assert econ["valuation_spot_usd"] == 100000
    assert econ["short_premium_usd_at_spot"] == pytest.approx(70)
    assert econ["long_premium_usd_at_spot"] == pytest.approx(30)
    assert econ["premium_spread_usd_at_spot"] == pytest.approx(40)
    assert econ["theoretical_two_leg_fee_usd_at_spot"] == pytest.approx(3)
    assert econ["net_credit_after_entry_fee_usd_at_spot"] == pytest.approx(37)
    assert econ["net_credit_after_fee_usd_at_spot"] == pytest.approx(36.25)
    assert econ["reference_margin_btc"] == pytest.approx(0.0001125)


def test_put_inverse_tail_is_not_capped_at_reference_scale():
    snap = u.evaluate(sample())
    tail = snap["liability"]["payout_table"][-1]["payout_btc"]
    assert tail > snap["liability"]["btc_reference_scale_at_spot"]
    assert u.inverse_spread_payout("put", 97000, 95000, 1000, 0.1) == pytest.approx(0.2)


def test_call_has_two_btc_breakeven_roots_and_a_finite_contract_peak():
    snap = u.evaluate(sample("call"))
    roots = snap["liability"]["breakeven_usd"]
    assert len(roots) == 2 and roots[0] < 105000 < roots[1]
    assert snap["liability"]["btc_contract_cap"] == pytest.approx(200 / 105000)


def test_missing_risk_or_fee_keeps_strict_margin_null():
    no_risk = u.evaluate(sample(risk=False))
    assert no_risk["economics"]["loss_budget_btc"] == pytest.approx(0.0003625)
    assert no_risk["economics"]["reference_margin_btc"] is None
    assert no_risk["status"] == "information_gap"
    no_fee_payload = sample()
    no_fee_payload["fee"] = {"basis": "unknown"}
    no_fee = u.evaluate(no_fee_payload)
    assert no_fee["economics"]["loss_budget_btc"] is None
    assert no_fee["economics"]["reference_margin_btc"] is None
    assert "fee_unknown" in no_fee["gap_reasons"]


@pytest.mark.parametrize("covers,reason", [
    ("entry", "fee_coverage_invalid"),
    (["entry"], "fee_coverage_incomplete"),
    (["entry", "delivery", "mystery"], "fee_coverage_invalid"),
])
def test_fee_coverage_must_explicitly_cover_holding_to_expiry(covers, reason):
    payload = sample()
    payload["fee"]["covers"] = covers
    snap = u.evaluate(payload)
    assert snap["fee"]["amount_btc"] is None
    assert snap["economics"]["loss_budget_btc"] is None
    assert snap["economics"]["reference_margin_btc"] is None
    assert reason in snap["gap_reasons"]


def test_fee_components_must_reconcile_to_total():
    payload = sample()
    payload["fee"].update(entry_fee_btc=0.00003, delivery_fee_btc=0.00001)
    snap = u.evaluate(payload)
    assert snap["fee"]["amount_btc"] is None
    assert "fee_components_invalid" in snap["gap_reasons"]


def test_quote_depth_clock_and_book_state_fail_closed():
    for mutate, reason in (
        (lambda p: p["market"]["long_book"].update(asks=[[0.003, 0.05]]), "long_depth_insufficient"),
        (lambda p: p["market"]["short_book"].update(timestamp=T - 6_000), "short_book_stale_or_future"),
        (lambda p: p["market"]["short_book"].update(state="locked"), "short_book_not_open"),
    ):
        payload = sample()
        mutate(payload)
        snap = u.evaluate(payload)
        assert snap["market"]["quote"]["credit_btc"] is None
        assert snap["economics"]["reference_margin_btc"] is None
        assert reason in snap["gap_reasons"]


def test_risk_must_match_candidate_time_scale_and_feature_clock():
    for key, value, reason in (("candidate_id", "wrong", "risk_candidate_mismatch"),
                               ("reference_price_usd", 99999, "risk_reference_price_mismatch"),
                               ("feature_end_ts_ms", T + 1, "risk_uses_future_input"),
                               ("horizon_hours", 12, "risk_horizon_mismatch")):
        payload = sample()
        payload["risk"][key] = value
        snap = u.evaluate(payload)
        assert snap["risk"]["status"] == "unusable"
        assert snap["economics"]["reference_margin_btc"] is None
        assert reason in snap["gap_reasons"]


def test_synthetic_mu_cannot_support_a_non_synthetic_candidate():
    payload = sample()
    payload["market"]["reference_source"] = "deribit_index"
    payload["data_identity"] = {"kind": "manual_input"}
    snap = u.evaluate(payload)
    assert snap["risk"]["status"] == "unusable"
    assert snap["economics"]["mu_ref_btc"] is None
    assert snap["economics"]["reference_margin_btc"] is None
    assert "synthetic_risk_requires_synthetic_data_identity" in snap["gap_reasons"]


def test_hand_supplied_current_fmz_fact_needs_btc_and_sha256_binding():
    payload = sample()
    payload["risk"]["source_kind"] = "frozen_model"
    payload["risk"]["source_fact_hash"] = "x"
    payload["fmz_fact_context"] = {"schema": "astra_fmz_fact_context@2.0.0",
                                   "status": "current", "underlying": "BTC",
                                   "source_hash": "x", "snapshot_ts_ms": T,
                                   "valuation_ts_ms": T, "price_observed_ms": T,
                                   "runtime_mode": "live_public_read_only",
                                   "current_price_usd": 100000,
                                   "current_price_source": "deribit_index",
                                   "market_facts": {}, "gap_reasons": []}
    with pytest.raises(ValueError, match="BTC identity and a sha256 source hash"):
        u.evaluate(payload)
    payload["fmz_fact_context"]["source_hash"] = "a" * 64
    payload["fmz_fact_context"]["underlying"] = "ETH"
    with pytest.raises(ValueError, match="BTC identity and a sha256 source hash"):
        u.evaluate(payload)
    payload["fmz_fact_context"]["underlying"] = "BTC"
    payload["fmz_fact_context"]["snapshot_ts_ms"] = T - 300_001
    with pytest.raises(ValueError, match="stale or not live read-only"):
        u.evaluate(payload)
    payload["fmz_fact_context"]["snapshot_ts_ms"] = T
    snap = u.evaluate(payload)
    assert snap["risk"]["status"] == "unusable"
    assert snap["economics"]["reference_margin_btc"] is None
    assert "risk_fmz_fact_binding_missing_or_stale" in snap["gap_reasons"]


def test_historical_replay_needs_a_frozen_source_hash():
    payload = sample(risk=False)
    payload["market"]["reference_source"] = "deribit_index"
    payload["data_identity"] = {"kind": "historical_replay"}
    with pytest.raises(ValueError, match="historical replay requires a source hash"):
        u.evaluate(payload)


def test_leg_contract_validation_and_origin_independent_snapshot_id():
    payload = sample()
    original = u.evaluate(payload)
    payload["origins"] = ["fixed_round", "natural_nr"]
    assert u.evaluate(payload)["snapshot_id"] == original["snapshot_id"]
    broken = sample()
    broken["metadata"]["long"]["expiration_timestamp"] += 1
    with pytest.raises(ValueError, match="instrument name and metadata expiry disagree"):
        u.evaluate(broken)
    broken = sample()
    broken["candidate"]["quantity_btc"] = 0.01
    with pytest.raises(ValueError, match="below an instrument minimum"):
        u.evaluate(broken)


def test_llm_packet_cannot_be_reattributed_after_numeric_change():
    snapshot = u.evaluate(sample())
    packet = u.llm_applicability_packet(snapshot)
    assert packet["snapshot_id"] == snapshot["snapshot_id"]
    assert packet["execution_allowed"] is False
    tampered = copy.deepcopy(snapshot)
    tampered["economics"]["reference_margin_btc"] = 999
    with pytest.raises(ValueError, match="changed since freeze"):
        u.llm_applicability_packet(tampered)
