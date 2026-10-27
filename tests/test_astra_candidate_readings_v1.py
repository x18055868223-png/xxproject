"""Candidate readings (docs/opus/23 §1.3, §2.2): one expected move, the pricing split, the clock price."""
import math

import pytest

from tools import astra_candidate_readings_v1 as R
from tools import astra_decision_path_v1 as dp
from tools import astra_entry_warnings_v1 as ew
from tools import astra_session_clock_v1 as clock

H = 3_600_000
NOW = 1_792_490_400_000            # Tue 2026-10-20 10:00 UTC
EXPIRY = 1_792_569_600_000         # Wed 2026-10-21 08:00 UTC (22 h left)
SPOT = 100_000.0
BY_EXPIRY = {"21OCT26": {"expiry": "21OCT26", "expiry_ms": EXPIRY, "atm_iv_pct": 30.0, "forward_usd": SPOT,
                         "age_ms": 5 * 60_000, "stale": False}}
GEX = {"term_structure": [{"expiry": "21OCT26", "days_to_expiry": 0.9, "atm_iv": 36.0},
                          {"expiry": "30OCT26", "days_to_expiry": 9.9, "atm_iv": 33.0}]}


def item(side="put", short=98_500.0, long=97_500.0, *, credit=0.0020, net=0.0017, marks=None, reduction=None,
         breakeven=None):
    out = {"snapshot": {
        "contract": {"side": side, "expiry_ms": EXPIRY, "short_strike_usd": short, "long_strike_usd": long,
                     "width_usd": abs(short - long), "quantity_btc": 1.0,
                     "short_instrument": f"BTC-21OCT26-{int(short)}-{'P' if side == 'put' else 'C'}",
                     "long_instrument": f"BTC-21OCT26-{int(long)}-{'P' if side == 'put' else 'C'}"},
        "market": {"reference_price_usd": SPOT},
        "economics": {"visible_credit_btc": credit, "net_credit_after_fee_btc": net,
                      "net_credit_after_fee_usd_at_spot": net * SPOT if net is not None else None},
        "fee": {"basis": "configured_assumption", "assumption_basis": "public_taker_capped_daily_delivery_zero",
                "amount_btc": 0.0003, "entry_fee_btc": 0.0003, "delivery_fee_btc": 0.0},
        "liability": {"breakeven_usd": breakeven if breakeven is not None else [short - 170.0 if side == "put" else short + 170.0]},
    }, "market_marks": marks if marks is not None else {"short_mark_btc": 0.0030, "long_mark_btc": 0.0008,
                                                         "short_mark_iv": 30.0, "long_mark_iv": 38.0}}
    if reduction is not None:
        out["decision_reduction"] = reduction
    return out


def build(it, **kw):
    kw.setdefault("now_ms", NOW)
    kw.setdefault("by_expiry", BY_EXPIRY)
    kw.setdefault("gex_info", GEX)
    kw.setdefault("realized_vol", None)
    kw.setdefault("history", [])
    return R.build(it, **kw)


def test_contract_keys_are_complete():
    out = build(item())
    assert out["schema"] == "candidate_readings@0.1" and out["at_ms"] == NOW
    assert set(out["expected_move"]) >= {
        "iv_pct", "iv_source", "iv_source_cn", "hours", "spot_usd", "move_pct", "move_usd", "short_strike_usd",
        "short_distance_pct", "short_distance_usd", "distance_in_moves", "band", "band_cn", "breakeven_usd",
        "breakeven_distance_pct", "breakeven_in_moves"}
    assert set(out["pricing"]) >= {
        "credit_btc", "credit_usd", "fee_btc", "fee_usd", "fee_assumed", "fee_basis_cn", "net_credit_btc",
        "net_credit_usd", "mark_spread_btc", "mark_spread_usd", "edge_usd", "spread_cost_usd", "fee_cost_usd",
        "short_mark_iv", "long_mark_iv", "skew_vol_pts", "skew_cost_usd", "width_usd", "loss_at_protection_usd",
        "worst_to_net_ratio", "tail_cn"}
    assert set(out["clock_price"]) >= {"iv_clock_adjusted", "ref_cycle_start", "ref_at_ms", "rv24_pct",
                                       "rv24_clock_adjusted", "basis_cn", "meaning_cn"}


def test_expected_move_uses_the_own_expiry_and_falls_back_to_the_term_row():
    move = build(item())["expected_move"]
    assert move["iv_pct"] == 30.0 and move["iv_source"] == "deribit_mark_own_expiry"
    assert move["iv_source_cn"] == "本期 Deribit 标记 ATM IV"
    assert move["hours"] == pytest.approx(22.0)
    assert move["move_pct"] == pytest.approx(30.0 * math.sqrt(22 / 8760))
    assert move["move_usd"] == pytest.approx(SPOT * move["move_pct"] / 100)
    # the profile's own 20-minute stale flag does not switch the source; only D2's age rule does
    flagged = build(item(), by_expiry={"21OCT26": {**BY_EXPIRY["21OCT26"], "stale": True}})["expected_move"]
    assert flagged["iv_source"] == "deribit_mark_own_expiry"
    stale = {"21OCT26": {**BY_EXPIRY["21OCT26"], "stale": True, "age_ms": dp.WALLS_MAX_AGE_MS + 1}}
    old = {"21OCT26": {**BY_EXPIRY["21OCT26"], "stale": False, "age_ms": dp.WALLS_MAX_AGE_MS + 1}}
    for rows in (stale, old, {}, {"21OCT26": {**BY_EXPIRY["21OCT26"], "atm_iv_pct": None}}):
        fallback = build(item(), by_expiry=rows)["expected_move"]
        assert fallback["iv_pct"] == 36.0 and fallback["iv_source"] == "gexmonitor_term"
        assert fallback["iv_source_cn"] == "期限结构 ATM IV（gexmonitor）"
    # no row of this expiry anywhere: the nearest term row, named as not this expiry (D2's rule)
    near = build(item(), by_expiry=stale, gex_info={"term_structure": [GEX["term_structure"][1]]})["expected_move"]
    assert near["iv_pct"] == 33.0 and near["iv_source"] == "gexmonitor_term_nearest" and "非本期" in near["iv_source_cn"]
    none = build(item(), by_expiry=stale, gex_info={})["expected_move"]
    assert none["iv_pct"] is None and none["iv_source"] is None and none["move_pct"] is None
    assert none["distance_in_moves"] is None and none["band"] is None and none["short_distance_pct"] == pytest.approx(1.5)
    # matched by the expiry token when the row has no expiry_ms
    token = build(item(), by_expiry={"21OCT26": {"atm_iv_pct": 31.0}})["expected_move"]
    assert token["iv_pct"] == 31.0


def test_distance_sign_and_bands_for_put_and_call():
    move = 30.0 * math.sqrt(22 / 8760)
    put = build(item("put", 98_500.0, 97_500.0))["expected_move"]
    assert put["short_distance_pct"] == pytest.approx(1.5) and put["short_distance_usd"] == pytest.approx(1500.0)
    assert put["distance_in_moves"] == pytest.approx(1.5 / move) and put["band"] == "wide" and put["band_cn"] == "较远"
    call = build(item("call", 101_000.0, 102_000.0))["expected_move"]
    assert call["short_distance_pct"] == pytest.approx(1.0) and call["distance_in_moves"] > 0
    assert build(item("put", 99_700.0, 98_700.0))["expected_move"]["band"] == "narrow"
    assert build(item("call", 100_700.0, 101_700.0))["expected_move"]["band_cn"] == "标准附近"
    inside = build(item("call", 99_500.0, 100_500.0))["expected_move"]
    assert inside["short_distance_pct"] < 0 and inside["band"] == "inside" and inside["band_cn"] == "已进入"
    assert R.NARROW_MOVES == dp.NARROW_MOVES
    # breakeven: outside the short leg by the net credit, positive and in moves
    be = put["breakeven_distance_pct"]
    assert put["breakeven_usd"] == 98_330.0 and be == pytest.approx(1.67) and put["breakeven_in_moves"] == pytest.approx(be / move)
    cbe = build(item("call", 101_000.0, 102_000.0))["expected_move"]
    assert cbe["breakeven_distance_pct"] == pytest.approx(1.17)


def test_the_d2_band_agrees_with_the_decision_path():
    for short in (99_700.0, 99_300.0, 98_500.0, 100_200.0):
        it = item("put", short, short - 1000)
        reading = build(it)["expected_move"]
        frame = {"at_ms": NOW, "candidate": it, "expiry_walls": BY_EXPIRY["21OCT26"]}
        d2 = dp._d2(frame, dp._geometry(frame))
        assert {"inside": "已进入", "narrow": "窄", "standard": "标准附近", "wide": "较远"}[reading["band"]] \
            == d2["lines"][0]["v"].split("：")[0]


def test_pricing_decomposition_identity():
    p = build(item())["pricing"]
    assert p["credit_usd"] == pytest.approx(200.0) and p["fee_btc"] == pytest.approx(0.0003)
    assert p["fee_usd"] == pytest.approx(30.0) and p["fee_cost_usd"] == pytest.approx(-30.0)
    assert p["fee_assumed"] is True and "taker" in p["fee_basis_cn"]
    assert p["net_credit_usd"] == pytest.approx(170.0)
    assert p["mark_spread_btc"] == pytest.approx(0.0022) and p["mark_spread_usd"] == pytest.approx(220.0)
    assert p["edge_usd"] == pytest.approx(-50.0) and p["spread_cost_usd"] == pytest.approx(-20.0)
    assert p["edge_usd"] == pytest.approx(p["spread_cost_usd"] + p["fee_cost_usd"])
    assert p["skew_vol_pts"] == pytest.approx(8.0)
    actual = build(item())
    actual_item = item()
    actual_item["snapshot"]["fee"] = {"basis": "actual", "amount_btc": 0.0003}
    assert build(actual_item)["pricing"]["fee_assumed"] is False
    assert actual["pricing"]["tail_cn"] == "跌破保护腿后按 BTC 计赔付仍增加（币本位）"
    assert build(item("call", 101_000.0, 102_000.0))["pricing"]["tail_cn"].startswith("涨过保护腿后按 USD 计封顶")


def test_skew_cost_sign():
    years = 22 / 8760
    flat = R.black76("put", SPOT, 97_500.0, years, 30.0) / SPOT
    rich = R.black76("put", SPOT, 97_500.0, years, 38.0) / SPOT
    marks = {"short_mark_btc": 0.0030, "short_mark_iv": 30.0}
    skewed = build(item(marks={**marks, "long_mark_btc": rich, "long_mark_iv": 38.0}))["pricing"]
    assert skewed["skew_cost_usd"] == pytest.approx((rich - flat) * SPOT) and skewed["skew_cost_usd"] > 0
    level = build(item(marks={**marks, "long_mark_btc": flat, "long_mark_iv": 30.0}))["pricing"]
    assert level["skew_cost_usd"] == pytest.approx(0.0, abs=1e-6)
    cheap = R.black76("put", SPOT, 97_500.0, years, 25.0) / SPOT
    assert build(item(marks={**marks, "long_mark_btc": cheap, "long_mark_iv": 25.0}))["pricing"]["skew_cost_usd"] < 0
    call_flat = R.black76("call", SPOT, 102_000.0, years, 30.0) / SPOT
    call_rich = R.black76("call", SPOT, 102_000.0, years, 34.0) / SPOT
    c = build(item("call", 101_000.0, 102_000.0, marks={**marks, "long_mark_btc": call_rich, "long_mark_iv": 34.0}))
    assert c["pricing"]["skew_cost_usd"] == pytest.approx((call_rich - call_flat) * SPOT)


def test_worst_to_net_ratio_from_r3_or_width():
    p = build(item())["pricing"]
    assert p["width_usd"] == 1000.0 and p["loss_at_protection_usd"] == pytest.approx(830.0)
    assert p["worst_to_net_ratio"] == pytest.approx(830.0 / 170.0)
    reduction = {"questions": [{"key": "worst_case", "numbers": {"loss_at_protection_usd": 900.0}}]}
    assert build(item(reduction=reduction))["pricing"]["loss_at_protection_usd"] == 900.0
    negative = build(item(net=-0.0001))["pricing"]
    assert negative["worst_to_net_ratio"] is None and negative["edge_usd"] == pytest.approx(-230.0)


def test_clock_price_reference_from_the_cycle_start():
    start = EXPIRY - 24 * H
    history = [{"at_ms": start + 10 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": 32.0},
               {"at_ms": start + 40 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": 34.0},
               {"at_ms": start + 50 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": 33.0},
               {"at_ms": start + 90 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": 60.0},     # after the first hour
               {"at_ms": start + 20 * 60_000, "expiry_ms": EXPIRY + 24 * H, "expiry_atm_iv_pct": 60.0},  # other expiry
               {"at_ms": start + 30 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": None}]
    rv = {"rv_24h_pct": 28.0, "last_bar_end_ms": NOW}
    c = build(item(), history=history, realized_vol=rv)["clock_price"]
    assert c["iv_clock_adjusted"] == pytest.approx(ew.clock_adjusted(30.0, NOW, EXPIRY))
    adjusted = sorted(ew.clock_adjusted(v, start + m * 60_000, EXPIRY) for v, m in ((32.0, 10), (34.0, 40), (33.0, 50)))
    assert c["ref_cycle_start"] == pytest.approx(adjusted[1]) and c["ref_at_ms"] == start + 10 * 60_000
    assert c["ref_samples"] == 3
    factor = clock.variance_between(NOW - 24 * H, NOW) / 24
    assert c["rv24_pct"] == 28.0 and c["rv24_clock_adjusted"] == pytest.approx(28.0 / math.sqrt(factor))
    assert "机制 B" in c["basis_cn"] and c["meaning_cn"]
    empty = build(item())["clock_price"]
    assert empty["ref_cycle_start"] is None and empty["ref_at_ms"] is None and empty["rv24_clock_adjusted"] is None


def test_missing_inputs_give_none_not_exceptions():
    bare = build({}, by_expiry=None, gex_info=None)
    assert bare["schema"] == R.SCHEMA and "error_type" not in bare
    assert all(v is None for k, v in bare["expected_move"].items())
    assert bare["pricing"]["edge_usd"] is None and bare["pricing"]["worst_to_net_ratio"] is None
    assert bare["clock_price"]["iv_clock_adjusted"] is None
    no_marks = build(item(marks={}))["pricing"]
    assert no_marks["mark_spread_usd"] is None and no_marks["edge_usd"] is None and no_marks["skew_cost_usd"] is None
    assert no_marks["net_credit_usd"] == pytest.approx(170.0)
    expired = build(item(), now_ms=EXPIRY + 1)["expected_move"]
    assert expired["move_pct"] is None and expired["distance_in_moves"] is None
    broken = R.build(None, now_ms=NOW, by_expiry=None, gex_info=None, realized_vol=None, history=None)
    assert broken == {"schema": R.SCHEMA, "at_ms": NOW, "error_type": "AttributeError"}
    weird = item()
    weird["snapshot"]["liability"]["breakeven_usd"] = "x"
    weird["market_marks"] = {"short_mark_btc": float("nan"), "long_mark_btc": True}
    out = build(weird)
    assert out["expected_move"]["breakeven_usd"] is None and out["pricing"]["mark_spread_btc"] is None


def test_an_incomplete_fee_never_becomes_a_net_credit():
    it = item(net=None)
    it["snapshot"]["fee"] = {"basis": "unknown", "amount_btc": None, "entry_fee_btc": 0.0003}   # coverage incomplete
    p = build(it)["pricing"]
    assert p["net_credit_btc"] is None and p["net_credit_usd"] is None and p["edge_usd"] is None
    assert p["fee_btc"] is None and p["worst_to_net_ratio"] is None


def test_skew_cost_does_not_move_with_a_forward_read_at_another_moment():
    a = build(item())["pricing"]["skew_cost_usd"]
    moved = {"21OCT26": {**BY_EXPIRY["21OCT26"], "forward_usd": SPOT * 1.0025}}
    assert build(item(), by_expiry=moved)["pricing"]["skew_cost_usd"] == pytest.approx(a)
    T = 22 / 8760
    assert a == pytest.approx(R.black76("put", SPOT, 97_500.0, T, 38.0) - R.black76("put", SPOT, 97_500.0, T, 30.0))
    assert a > 0


def test_the_cycle_start_reference_only_compares_the_same_iv_source():
    start = EXPIRY - 24 * H
    history = [{"at_ms": start + 10 * 60_000, "expiry_ms": EXPIRY, "expiry_atm_iv_pct": 28.0}]
    own = build(item(), history=history)["clock_price"]
    assert own["ref_cycle_start"] == pytest.approx(ew.clock_adjusted(28.0, start + 10 * 60_000, EXPIRY))
    old = {"21OCT26": {**BY_EXPIRY["21OCT26"], "age_ms": dp.WALLS_MAX_AGE_MS + 1}}
    term = build(item(), by_expiry=old, history=history)
    assert term["expected_move"]["iv_source"] == "gexmonitor_term"
    assert term["clock_price"]["ref_cycle_start"] is None and term["clock_price"]["iv_clock_adjusted"] is not None


def test_the_card_and_d2_read_the_same_ratio():
    for rows, gex in ((BY_EXPIRY, GEX), ({"21OCT26": {**BY_EXPIRY["21OCT26"], "age_ms": dp.WALLS_MAX_AGE_MS + 1}}, GEX),
                      ({}, {"term_structure": [GEX["term_structure"][1]]}), ({}, {"atm_iv": 41.0})):
        it = item()
        frame = {"candidate": it, "at_ms": NOW, "expiry_walls": next(iter(rows.values()), None),
                 "market_facts": {"space_context": {"gex_info": gex}}}
        g = dp._geometry(frame)
        move = build(it, by_expiry=rows, gex_info=gex)["expected_move"]
        assert move["iv_pct"] == pytest.approx(g["iv"]) and move["distance_in_moves"] == pytest.approx(g["ratio"])
