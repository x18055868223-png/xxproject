import hashlib

import pytest

from tools import astra_underwriting_v20 as underwriting
from tools.astra_map_presentation_v241 import build_presentation


T = 1_790_323_200_000
EXPIRY = T + 86_400_000


def _payload(*, short_bids=None, short_asks=None, long_asks=None):
    side = "put"
    short_name = "BTC-26SEP26-97000-P"
    long_name = "BTC-26SEP26-95000-P"

    def meta(name, strike):
        return {
            "instrument_name": name,
            "kind": "option",
            "base_currency": "BTC",
            "settlement_currency": "BTC",
            "quote_currency": "BTC",
            "counter_currency": "USD",
            "price_index": "btc_usd",
            "option_type": side,
            "state": "open",
            "expiration_timestamp": EXPIRY,
            "contract_size": 1,
            "strike": strike,
            "min_trade_amount": 0.1,
        }

    return {
        "candidate": {
            "short_instrument": short_name,
            "long_instrument": long_name,
            "quantity_btc": 1.0,
            "exit_policy": "hold_to_expiry",
        },
        "metadata": {"short": meta(short_name, 97000), "long": meta(long_name, 95000)},
        "market": {
            "valuation_ts_ms": T,
            "received_ts_ms": T,
            "reference_price_usd": 100000,
            "reference_source": "synthetic_fixture",
            "short_book": {
                "instrument_name": short_name,
                "state": "open",
                "timestamp": T,
                "bids": short_bids or [[0.006, 1.0]],
                "asks": short_asks or [[0.007, 1.0]],
            },
            "long_book": {
                "instrument_name": long_name,
                "state": "open",
                "timestamp": T,
                "bids": [[0.001, 1.0]],
                "asks": long_asks or [[0.002, 1.0]],
            },
        },
        "fee": {
            "amount_btc": 0.0001,
            "basis": "configured_assumption",
            "entry_fee_btc": 0.00008,
            "delivery_fee_btc": 0.00002,
            "covers": ["entry", "delivery"],
        },
        "origins": ["manual"],
    }


def test_small_crossed_book_is_rejected_from_raw_valid_best_levels():
    payload = _payload(short_bids=[[0.006001, 1]], short_asks=[[0.006000, 1]])

    snap = underwriting.evaluate(payload)

    assert snap["market"]["quote"]["credit_btc"] is None
    assert "short_crossed_book" in snap["gap_reasons"]


def test_equal_book_and_invalid_size_level_remain_usable_when_depth_exists():
    payload = _payload(short_bids=[[0.008, 0], [0.006, 1]], short_asks=[[0.006, 1]])

    snap = underwriting.evaluate(payload)

    assert "short_crossed_book" not in snap["gap_reasons"]
    assert snap["market"]["quote"]["credit_btc"] == pytest.approx(0.004)


@pytest.mark.parametrize("unit", [None, "BTC", " btc "])
def test_missing_or_btc_quantity_unit_keeps_existing_btc_contract(unit):
    payload = _payload()
    if unit is not None:
        payload["candidate"]["quantity_unit"] = unit

    snap = underwriting.evaluate(payload)

    assert snap["contract"]["quantity_btc"] == 1.0
    assert snap["candidate_id"] == snap["contract"]["candidate_id"]


def test_declared_non_btc_quantity_unit_is_rejected():
    payload = _payload()
    payload["candidate"]["quantity_unit"] = "contracts"

    with pytest.raises(ValueError, match="quantity_unit must be BTC"):
        underwriting.evaluate(payload)


def _presentation_map():
    return {
        "schema": "btc_map@1",
        "map_id": "map-r1",
        "current_price_usd": 100000,
        "price_basis": "deribit_index",
        "background": {},
        "reference_pool": [],
        "policy_link": {},
    }


def _presentation_snapshot(*, split_fee: bool):
    fee = {
        "amount_btc": 0.0001,
        "basis": "configured_assumption",
        "covers": ["entry", "delivery"],
    }
    economics = {
        "visible_credit_btc": 0.004,
        "net_credit_after_fee_btc": 0.0039,
    }
    if split_fee:
        fee["entry_fee_btc"] = 0.00008
        fee["delivery_fee_btc"] = 0.00002
        economics["theoretical_two_leg_fee_btc"] = 0.00008
        economics["net_credit_after_entry_fee_btc"] = 0.00392
    return {
        "schema": underwriting.SCHEMA,
        "snapshot_id": "snap-r1",
        "candidate_id": hashlib.sha256(b"candidate").hexdigest()[:24],
        "contract": {
            "side": "put",
            "short_strike_usd": 97000,
            "long_strike_usd": 95000,
            "quantity_btc": 1.0,
            "expiry_ms": EXPIRY,
        },
        "data_identity": {"kind": "synthetic_fixture"},
        "market": {
            "valuation_ts_ms": T,
            "reference_price_usd": 100000,
            "reference_source": "synthetic_fixture",
        },
        "fee": fee,
        "economics": economics,
        "liability": {},
        "llm_review": {"status": "not_requested", "reviewed_snapshot_id": None},
    }


def test_unknown_entry_fee_does_not_relabel_total_hold_fee():
    presentation = build_presentation(_presentation_map(), _presentation_snapshot(split_fee=False))
    identity = presentation["candidate_identity"]

    assert identity["entry_fee_btc"] is None
    assert identity["total_hold_fee_btc"] == pytest.approx(0.0001)
    assert identity["net_credit_after_fee_btc"] == pytest.approx(0.0039)


def test_split_entry_fee_and_total_hold_fee_are_separate_fields():
    presentation = build_presentation(_presentation_map(), _presentation_snapshot(split_fee=True))
    identity = presentation["candidate_identity"]

    assert identity["entry_fee_btc"] == pytest.approx(0.00008)
    assert identity["total_hold_fee_btc"] == pytest.approx(0.0001)
    assert identity["net_credit_after_entry_fee_btc"] == pytest.approx(0.00392)
