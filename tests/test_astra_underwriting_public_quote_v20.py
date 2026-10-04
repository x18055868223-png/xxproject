import copy

import pytest

from tools import astra_underwriting_public_quote_v20 as q
from tools import astra_underwriting_v20 as u
from test_astra_underwriting_v20 import T, sample


def test_only_explicit_public_endpoints_and_missing_economics_stay_null():
    fixture = sample(risk=False)
    names = {"short": fixture["candidate"]["short_instrument"],
             "long": fixture["candidate"]["long_instrument"]}
    calls = []

    def fetch(path, params):
        calls.append((path, dict(params)))
        leg = "short" if params["instrument_name"] == names["short"] else "long"
        if path == "public/get_instrument":
            return copy.deepcopy(fixture["metadata"][leg])
        book = copy.deepcopy(fixture["market"][leg + "_book"])
        book["index_price"] = 100000
        return book

    payload = q.capture_candidate(names["short"], names["long"], 0.1,
                                  transport=fetch, clock_ms=lambda: T)
    assert len(calls) == 4
    assert {path for path, _ in calls} == {"public/get_instrument", "public/get_order_book"}
    assert all(params.get("depth") == 5 for path, params in calls if path.endswith("get_order_book"))
    snap = u.evaluate(payload)
    assert snap["market"]["quote"]["credit_btc"] == pytest.approx(0.0004)
    assert snap["market"]["reference_source"] == "deribit_index"
    assert snap["economics"]["reference_margin_btc"] is None
    assert snap["fee"]["basis"] == "unknown"


def test_public_adapter_rejects_unrelated_names_and_mismatched_response():
    with pytest.raises(ValueError, match="full Deribit BTC option names"):
        q.capture_candidate("ETH-x", "BTC-x", 0.1, transport=lambda *_: {})
    with pytest.raises(ValueError, match="only audited public"):
        q.public_transport("private/buy", {})
    fixture = sample(risk=False)
    with pytest.raises(ValueError, match="metadata is missing"):
        q.capture_candidate(fixture["candidate"]["short_instrument"],
                            fixture["candidate"]["long_instrument"], 0.1,
                            transport=lambda *_: {}, clock_ms=lambda: T)


def test_reference_index_disagreement_invalidates_visible_quote():
    fixture = sample(risk=False)
    fixture["market"]["reference_source"] = "deribit_index"
    fixture["market"]["short_book"]["index_price"] = 100000
    fixture["market"]["long_book"]["index_price"] = 101000
    snap = u.evaluate(fixture)
    assert snap["market"]["quote"]["credit_btc"] is None
    assert "leg_index_mismatch" in snap["gap_reasons"]


def test_public_daily_fee_is_a_capped_taker_assumption_not_an_account_fee():
    fixture = sample(risk=False)
    names = {leg: fixture["candidate"][f"{leg}_instrument"] for leg in ("short", "long")}
    for leg in ("short", "long"):
        fixture["metadata"][leg]["taker_commission"] = 0.0003
        fixture["metadata"][leg]["settlement_period"] = "day"

    def fetch(path, params):
        leg = "short" if params["instrument_name"] == names["short"] else "long"
        if path == "public/get_instrument":
            return copy.deepcopy(fixture["metadata"][leg])
        book = copy.deepcopy(fixture["market"][leg + "_book"])
        book["index_price"] = 100000
        return book

    payload = q.capture_candidate(names["short"], names["long"], 0.1,
                                  transport=fetch, clock_ms=lambda: T)
    assert payload["fee"]["basis"] == "configured_assumption"
    assert payload["fee"]["covers"] == ["entry", "delivery"]
    assert payload["fee"]["entry_fee_btc"] == pytest.approx(0.00006)
    assert payload["fee"]["delivery_fee_btc"] == 0
    assert len(payload["data_identity"]["source_sha256"]) == 64
    snap = u.evaluate(payload)
    assert snap["economics"]["loss_budget_btc"] == pytest.approx(0.00034)
    assert snap["economics"]["mu_ref_btc"] is None
    assert snap["economics"]["reference_margin_btc"] is None

    fixture["metadata"]["long"]["settlement_period"] = "week"
    non_daily = q.capture_candidate(names["short"], names["long"], 0.1,
                                    transport=fetch, clock_ms=lambda: T)
    assert non_daily["fee"]["basis"] == "unknown"
