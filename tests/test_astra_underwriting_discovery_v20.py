import pytest

from tools import astra_underwriting_discovery_v20 as d
from tools import astra_underwriting_v20 as u


T = 1_790_323_200_000
EXP_24H = T + 86_400_000
EXP_48H = T + 2 * 86_400_000


def option(strike, side, *, expiry=EXP_24H, label="26SEP26", state="open",
           min_trade=0.1, name_strike=None, option_type=None, delta=None,
           delta_ts=T, delta_missing=False):
    suffix = "P" if side == "put" else "C"
    name = f"BTC-{label}-{int(name_strike if name_strike is not None else strike)}-{suffix}"
    row = {"instrument_name": name, "kind": "option", "base_currency": "BTC",
           "settlement_currency": "BTC", "quote_currency": "BTC", "counter_currency": "USD",
           "price_index": "btc_usd", "option_type": option_type or side,
           "instrument_type": "reversed", "state": state,
           "expiration_timestamp": expiry, "contract_size": 1,
           "strike": strike, "min_trade_amount": min_trade}
    if not delta_missing:
        row["greeks"] = {"delta": (-0.35 if side == "put" else 0.35) if delta is None else delta}
        row["delta_observed_at_ms"] = delta_ts
        row["delta_source"] = "test.greeks.delta"
    return row


def base_chain():
    return [
        option(99000, "put"),
        option(97000, "put"),
        option(101000, "call"),
        option(103000, "call"),
        option(80000, "put", expiry=EXP_48H, label="27SEP26"),
        option(78000, "put", expiry=EXP_48H, label="27SEP26"),
        option(120000, "call", expiry=EXP_48H, label="27SEP26"),
        option(122000, "call", expiry=EXP_48H, label="27SEP26"),
    ]


def test_discover_parallel_candidates_are_exact_width_and_v20_compatible():
    result = d.discover(base_chain(), 100000, T)
    assert result["schema"] == d.SCHEMA
    assert result["entry_mode"] == "parallel_read_only_no_side_selection"
    assert result["selected_expiry_ms"] == EXP_24H
    assert result["remaining_hours"] == pytest.approx(24)
    put = result["candidates"]["put"]
    call = result["candidates"]["call"]
    assert put["status"] == "candidate"
    assert put["candidate"] == {"short_instrument": "BTC-26SEP26-99000-P",
                                "long_instrument": "BTC-26SEP26-97000-P",
                                "quantity_btc": 1.0,
                                "exit_policy": "hold_to_expiry"}
    assert call["status"] == "candidate"
    assert call["candidate"]["short_instrument"] == "BTC-26SEP26-101000-C"
    assert call["candidate"]["long_instrument"] == "BTC-26SEP26-103000-C"
    assert put["width_usd"] == 2000
    assert call["width_usd"] == 2000
    assert put["short_abs_delta"] == pytest.approx(0.35)
    assert call["short_abs_delta"] == pytest.approx(0.35)
    assert put["short_delta_target_abs"] == 0.35
    assert put["short_delta_max_abs"] == 0.5
    assert put["selection_basis"] == "short_abs_delta_closest_to_target_with_exact_width_and_abs_delta_lte_max"
    assert u.candidate_identity(put["candidate"], put["metadata"], T)["side"] == "put"
    assert u.candidate_identity(call["candidate"], call["metadata"], T)["side"] == "call"


def test_short_legs_are_strictly_otm_not_at_the_index():
    chain = [
        option(100000, "put"),
        option(98000, "put"),
        option(96000, "put"),
        option(100000, "call"),
        option(102000, "call"),
        option(104000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    assert result["candidates"]["put"]["candidate"]["short_instrument"] == "BTC-26SEP26-98000-P"
    assert result["candidates"]["put"]["candidate"]["long_instrument"] == "BTC-26SEP26-96000-P"
    assert result["candidates"]["call"]["candidate"]["short_instrument"] == "BTC-26SEP26-102000-C"
    assert result["candidates"]["call"]["candidate"]["long_instrument"] == "BTC-26SEP26-104000-C"


def test_missing_exact_protection_leg_is_a_gap_without_widening():
    chain = [
        option(99000, "put"),
        option(96000, "put"),
        option(101000, "call"),
        option(103000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    put = result["candidates"]["put"]
    assert put["status"] == "gap"
    assert put["candidate"] is None
    assert "protection_leg_missing_exact_width" in put["gap_reasons"]
    assert result["candidates"]["call"]["status"] == "candidate"


def test_short_leg_selection_uses_delta_not_spot_distance_and_ties_are_stable():
    chain = [
        option(99000, "put", delta=-0.20),
        option(97000, "put", delta=-0.36),
        option(95000, "put"),
        option(98000, "put", delta=-0.34),
        option(96000, "put"),
        option(101000, "call", delta=0.20),
        option(103000, "call", delta=0.36),
        option(105000, "call"),
        option(102000, "call", delta=0.34),
        option(104000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    # 0.34 and 0.36 are equally close to the requested 0.35; lower |delta|
    # wins deterministically before strike distance.
    put = result["candidates"]["put"]
    call = result["candidates"]["call"]
    assert put["candidate"]["short_instrument"] == "BTC-26SEP26-98000-P"
    assert put["candidate"]["long_instrument"] == "BTC-26SEP26-96000-P"
    assert put["short_abs_delta"] == pytest.approx(0.34)
    assert call["candidate"]["short_instrument"] == "BTC-26SEP26-102000-C"
    assert call["candidate"]["long_instrument"] == "BTC-26SEP26-104000-C"
    assert call["short_abs_delta"] == pytest.approx(0.34)


@pytest.mark.parametrize("mutate,reason", [
    (lambda row: row.update(greeks={}), "short_delta_missing_or_invalid"),
    (lambda row: row.update(greeks={"delta": "bad"}), "short_delta_missing_or_invalid"),
    (lambda row: row.update(delta_observed_at_ms=T - d.DEFAULT_DELTA_MAX_AGE_MS - 1), "short_delta_stale_or_future"),
    (lambda row: row.update(greeks={"delta": -0.55}), "short_delta_abs_above_max"),
    (lambda row: row.update(greeks={"delta": 0.35}), "short_delta_sign_mismatch"),
])
def test_short_delta_missing_stale_invalid_or_above_half_fails_closed(mutate, reason):
    short = option(99000, "put")
    mutate(short)
    chain = [short, option(97000, "put"), option(101000, "call"), option(103000, "call")]
    result = d.discover(chain, 100000, T)
    put = result["candidates"]["put"]
    assert put["status"] == "gap"
    assert reason in put["gap_reasons"]
    assert put["candidate"] is None
    assert result["candidates"]["call"]["status"] == "candidate"


def test_non_open_leg_is_not_selected_and_is_reported_as_invalid():
    chain = [
        option(99000, "put"),
        option(97000, "put", state="closed"),
        option(101000, "call"),
        option(103000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    assert result["candidates"]["put"]["status"] == "gap"
    assert "protection_leg_missing_exact_width" in result["candidates"]["put"]["gap_reasons"]
    invalid = result["input_summary"]["invalid_instruments"]
    assert invalid[0]["instrument_name"] == "BTC-26SEP26-97000-P"
    assert "state_not_open" in invalid[0]["reasons"]


def test_wrong_identity_cannot_fill_the_protection_slot():
    chain = [
        option(99000, "put"),
        option(97000, "put", name_strike=96000),
        option(101000, "call"),
        option(103000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    assert result["candidates"]["put"]["status"] == "gap"
    assert "protection_leg_missing_exact_width" in result["candidates"]["put"]["gap_reasons"]
    invalid = result["input_summary"]["invalid_instruments"]
    assert invalid[0]["instrument_name"] == "BTC-26SEP26-96000-P"
    assert "name_strike_mismatch" in invalid[0]["reasons"]


def test_quantity_below_leg_minimum_blocks_candidate():
    chain = [
        option(99000, "put"),
        option(97000, "put", min_trade=2),
        option(101000, "call"),
        option(103000, "call"),
    ]
    result = d.discover(chain, 100000, T)
    put = result["candidates"]["put"]
    assert put["status"] == "gap"
    assert "quantity_below_min_trade" in put["gap_reasons"]
    assert result["candidates"]["call"]["status"] == "candidate"


def test_no_future_open_btc_inverse_expiry_returns_parallel_gaps():
    chain = [
        option(99000, "put", expiry=T - 1, label="25SEP26"),
        option(101000, "call", expiry=T - 1, label="25SEP26"),
    ]
    result = d.discover(chain, 100000, T)
    assert result["selected_expiry_ms"] is None
    assert result["candidates"]["put"]["gap_reasons"] == ["expiry_missing"]
    assert result["candidates"]["call"]["gap_reasons"] == ["expiry_missing"]


def test_expiry_tie_chooses_the_earlier_remaining_time():
    captured = T + 3_600_000
    chain = [
        option(99000, "put"),
        option(97000, "put"),
        option(101000, "call"),
        option(103000, "call"),
        option(98000, "put", expiry=EXP_48H, label="27SEP26"),
        option(96000, "put", expiry=EXP_48H, label="27SEP26"),
        option(102000, "call", expiry=EXP_48H, label="27SEP26"),
        option(104000, "call", expiry=EXP_48H, label="27SEP26"),
    ]
    result = d.discover(chain, 100000, captured, target_hours=35)
    assert result["selected_expiry_ms"] == EXP_24H
    assert result["remaining_hours"] == pytest.approx(23)


def test_public_discovery_failed_delta_population_keeps_affected_side_unselected(monkeypatch):
    monkeypatch.setattr(d.time, "time", lambda: T / 1000)

    def get(_base, path, params, _timeout):
        if path.endswith("get_index_price"):
            return {"result": {"index_price": 100000}}
        if path.endswith("get_instruments"):
            return {"result": base_chain()}
        name = params["instrument_name"]
        if name.endswith("-P"):
            raise RuntimeError("bounded provider budget exhausted")
        return {"result": {"instrument_name": name, "timestamp": T, "greeks": {"delta": .35}}}

    result = d.capture_public_deribit_discovery(transport=get, captured_ts_ms=T)
    assert result["candidates"]["put"]["status"] == "gap"
    assert result["candidates"]["put"]["gap_reasons"] == ["delta_population_capture_incomplete"]
    assert result["candidates"]["call"]["status"] == "candidate"
    assert result["data_identity"]["delta_capture"]["order_book_errors"]


def test_public_discovery_records_receipts_and_never_chooses_a_side(monkeypatch):
    calls = []

    def get(_base, path, params, _timeout):
        calls.append((path, dict(params)))
        if path.endswith("get_index_price"):
            return {"result": {"index_price": 100000}}
        if path.endswith("get_instruments"):
            return {"result": [
                {key: value for key, value in item.items()
                 if key not in {"greeks", "delta_observed_at_ms", "delta_source"}}
                for item in base_chain()
            ]}
        name = params["instrument_name"]
        suffix = name.rsplit("-", 1)[-1]
        strike = int(name.rsplit("-", 2)[1])
        delta = -0.35 if suffix == "P" and strike == 99000 else 0.35 if suffix == "C" and strike == 101000 else -0.1 if suffix == "P" else 0.1
        return {"result": {"instrument_name": name, "timestamp": T,
                           "greeks": {"delta": delta}}}

    times = iter([(T - 1000) / 1000, T / 1000, *((T / 1000) for _ in range(8))])
    monkeypatch.setattr(d, "_public_get", get)
    monkeypatch.setattr(d.time, "time", lambda: next(times))
    result = d.capture_public_deribit_discovery()
    assert [path for path, _ in calls][:2] == ["/public/get_index_price", "/public/get_instruments"]
    assert {path for path, _ in calls[2:]} == {"/public/get_order_book"}
    assert len(calls) == 4  # only two candidate short books after metadata/index.
    assert result["captured_ts_ms"] == T
    assert result["data_identity"]["request_started_ms"] == T - 1000
    assert result["data_identity"]["index_received_ms"] == T
    assert result["data_identity"]["instrument_received_ms"] == T
    assert "order_book_response_hashes" in result["data_identity"]["delta_capture"]
    assert result["data_identity"]["delta_capture"]["requested_count"] == 2
    assert result["entry_mode"] == "parallel_read_only_no_side_selection"
    assert set(result["candidates"]) == {"put", "call"}
    assert result["candidates"]["put"]["short_delta_source"] == "public/get_order_book.greeks.delta"


def test_public_discovery_uses_capture_completion_time_not_request_start_for_delta_freshness(monkeypatch):
    calls = []

    def get(_base, path, params, _timeout):
        calls.append((path, dict(params)))
        if path.endswith("get_index_price"):
            return {"result": {"index_price": 100000}}
        if path.endswith("get_instruments"):
            return {"result": [
                {key: value for key, value in item.items()
                 if key not in {"greeks", "delta_observed_at_ms", "delta_source"}}
                for item in base_chain()
            ]}
        name = params["instrument_name"]
        suffix = name.rsplit("-", 1)[-1]
        return {"result": {"instrument_name": name, "timestamp": T + 1000,
                           "greeks": {"delta": -0.35 if suffix == "P" else 0.35}}}

    times = iter([(T - 9000) / 1000, (T - 8000) / 1000, (T + 1500) / 1000, (T + 2000) / 1000])
    monkeypatch.setattr(d, "_public_get", get)
    monkeypatch.setattr(d.time, "time", lambda: next(times))

    result = d.capture_public_deribit_discovery(captured_ts_ms=T - 10000)

    assert result["data_identity"]["request_started_ms"] == T - 10000
    assert result["data_identity"]["requested_capture_ts_ms"] == T - 10000
    assert result["data_identity"]["capture_completed_ms"] == T + 2000
    assert result["captured_ts_ms"] == T + 2000
    assert result["candidates"]["put"]["status"] == "candidate"
    assert result["candidates"]["call"]["status"] == "candidate"
    assert result["candidates"]["put"]["short_delta_observed_at_ms"] == T + 1000


def test_public_discovery_requests_only_metadata_qualified_short_candidates(monkeypatch):
    chain = []
    for strike in range(89000, 100000, 1000):  # Put shorts 91k..99k => 9 qualified.
        chain.append(option(strike, "put", delta_missing=True))
    for strike in range(100000, 117000, 1000):  # ITM puts; never short candidates.
        chain.append(option(strike, "put", delta_missing=True))
    for strike in range(101000, 116000, 1000):  # Call shorts 101k..113k => 13 qualified.
        chain.append(option(strike, "call", delta_missing=True))
    for strike in range(84000, 101000, 1000):  # ITM calls; never short candidates.
        chain.append(option(strike, "call", delta_missing=True))
    assert len(chain) == 60
    calls = []

    def get(_base, path, params, _timeout):
        calls.append((path, dict(params)))
        if path.endswith("get_index_price"):
            return {"result": {"index_price": 100000}}
        if path.endswith("get_instruments"):
            return {"result": chain}
        name = params["instrument_name"]
        suffix = name.rsplit("-", 1)[-1]
        return {"result": {"instrument_name": name, "timestamp": T,
                           "greeks": {"delta": -0.35 if suffix == "P" else 0.35}}}

    monkeypatch.setattr(d, "_public_get", get)
    monkeypatch.setattr(d.time, "time", lambda: T / 1000)

    result = d.capture_public_deribit_discovery(captured_ts_ms=T)
    requested = [params["instrument_name"] for path, params in calls if path.endswith("get_order_book")]

    assert len(calls) == 24
    assert len(requested) == 22
    assert sum(name.endswith("-P") for name in requested) == 9
    assert sum(name.endswith("-C") for name in requested) == 13
    assert result["data_identity"]["delta_short_candidate_counts"] == {"put": 9, "call": 13}
    assert result["data_identity"]["delta_capture"]["requested_count"] == 22
