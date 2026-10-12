"""Review evidence consumes the gexmonitor option data with program relations."""
import math

import pytest

from tools import astra_underwriting_fact_context_v20 as f
from tools import astra_wpem_product_review_v1 as product
from tools import astra_underwriting_review_v20 as legacy
from test_astra_underwriting_fact_context_v20 import T, rich_fmz_row

SPOT = 83829.52
CONTRACT = {"side": "put", "short_instrument": "BTC-13OCT26-83500-P", "long_instrument": "BTC-13OCT26-81500-P",
            "short_strike_usd": 83500.0, "long_strike_usd": 81500.0}
GEX = {
    "support_walls": [79000.0, 78000.0], "resistance_walls": [90000.0],
    "magnet_price": 90000.0, "volatility_trigger": 90000.0,
    "near_term_walls": {"weekly": {"magnet_price": 84000.0, "walls": {
        "p1": {"strike": 84000.0, "gex_usd": 14.5e6}, "n1": {"strike": 83000.0, "gex_usd": -11.2e6}}}},
    "max_pain_price": 78000.0, "max_pain_target_expiry": "30OCT26",
    "next_expiry": {"expiry": "13OCT26", "max_pain_price": 82000.0, "hours_to_expiry": 29.4},
    "hedging_flows": {"scenarios": [{"price_change_pct": -1.0, "hedging_usd": 1.4e8},
                                    {"price_change_pct": 1.0, "hedging_usd": -1.4e8},
                                    {"price_change_pct": -3.0, "hedging_usd": 1.4e8}]},
    "atm_iv": 29.38, "iv_percentile": 24.0, "dvol": 37.34,
    "term_structure": [{"expiry": "14OCT26", "days_to_expiry": 1.0, "atm_iv": 29.38},
                       {"expiry": "16OCT26", "days_to_expiry": 3.0, "atm_iv": 33.52}],
    "rr_skew": [{"tenor": "1d", "days": 1.0, "expiry": "12OCT26", "put_25d_iv": 24.6, "call_25d_iv": 27.0, "rr": 2.35}],
    "pcr": 0.687, "pcr_oi": 0.695,
    "whale_24h": {"trade_count": 42, "buy_put_count": 9, "sell_put_count": 3, "active_strike": 82000.0,
                  "net_flow_usd": -211329.7, "lead_expiry": "16OCT26"},
}
SNAPSHOT = {"fact_context": {"market_facts": {"space_context": {"gex_info": GEX}}},
            "liability": {"breakeven_usd": [83249.21], "btc_reference_scale_at_spot": 2000.0 / SPOT}}


def units():
    return {u["ref"]: u for u in product._options_units(
        SNAPSHOT, CONTRACT, horizon_hours=29.41, spot=SPOT, credit_btc=0.0034)}


def test_fact_context_keeps_gexmonitor_option_fields():
    row = rich_fmz_row()
    row["factor_snapshot"]["gex_info"].update({k: GEX[k] for k in (
        "near_term_walls", "max_pain_price", "atm_iv", "term_structure", "whale_24h", "hedging_flows")})
    info = f.from_evaluation_snapshot(row, T, source_hash="9" * 64)["market_facts"]["space_context"]["gex_info"]
    assert info["near_term_walls"]["weekly"]["walls"]["p1"]["strike"] == 84000.0
    assert info["atm_iv"] == 29.38 and info["max_pain_price"] == 78000.0
    assert info["whale_24h"]["trade_count"] == 42 and info["hedging_flows"]["scenarios"]


def test_structure_levels_are_placed_in_the_contract_payoff_geometry():
    s = units()["options.structure"]["facts"]
    support = s["all_expiry_walls"]["support_walls"]
    assert support[0] == {"price": 79000.0, "on_adverse_side_of_spot": True, "liability_zone": "beyond_long"}
    weekly = s["near_term_walls"]["weekly"]["walls"]
    assert weekly["p1"]["on_adverse_side_of_spot"] is False and weekly["p1"]["liability_zone"] == "outside"
    assert weekly["n1"]["liability_zone"] == "short_to_long"
    assert s["max_pain"]["same_expiry_as_contract"] is False
    assert s["next_expiry_max_pain"]["same_expiry_as_contract"] is True
    hedging = s["dealer_hedging_adverse_scenarios"]
    assert [row["price_change_pct"] for row in hedging["scenarios"]] == [-1.0, -3.0]
    assert hedging["move_to_short_strike_pct"] == pytest.approx((83500 - SPOT) / SPOT * 100)


def test_vol_relations_standardize_distance_without_probability():
    v = units()["options.vol"]
    assert v["facts"]["atm_iv_for_horizon"]["match"] == "nearest_listed_tenor"
    sigma = 29.38 * math.sqrt(29.41 / 8760)
    rel = v["relations"]
    assert rel["horizon_sigma_move_pct"] == pytest.approx(sigma)
    assert rel["short_strike_distance_in_sigma"] == pytest.approx(((SPOT - 83500) / SPOT * 100) / sigma)
    assert 0 < rel["short_strike_distance_in_sigma"] < rel["breakeven_distance_in_sigma"]
    assert rel["credit_to_width_at_spot"] == pytest.approx(0.0034 / (2000 / SPOT))
    assert "不是概率" in v["basis"]


def test_call_side_geometry_mirrors_put():
    call = dict(CONTRACT, side="call", short_strike_usd=84500.0, long_strike_usd=86500.0)
    pos = product._liability_position(85000.0, side="call", spot=SPOT, short=84500.0, long=86500.0)
    assert pos == {"price": 85000.0, "on_adverse_side_of_spot": True, "liability_zone": "short_to_long"}
    zone = product._zone_position(84400.0, 84600.0, side="call", spot=SPOT, short=84500.0, long=86500.0)
    assert zone["contains_short_strike"] and not zone["contains_spot"]
    assert product._options_units({}, call, horizon_hours=10, spot=SPOT, credit_btc=0.001) == []


def test_classification_negation_does_not_need_the_topic_ref():
    text = "这些只属方向或需求背景，不是补偿或费用变化；不能把它们当作补偿改善或费用变化的证据。"
    product._validate_topic_direct_refs(text, ["contract", "flow", "tmv", "skew"])
    for absence in ("费用并未增加。", "并未出现巨鲸卖压。"):
        with pytest.raises(legacy.ReviewValidationError):
            product._validate_topic_direct_refs(absence, ["contract"])


def test_option_topics_require_their_units():
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_topic_direct_refs("近端期权墙位于短腿上方。", ["contract"])
    product._validate_topic_direct_refs("近端期权墙位于短腿上方。", ["contract", "options.structure"])
    product._validate_topic_direct_refs("短腿按隐含波动不足四分之一个σ，25d偏斜偏向Call。", ["options.vol"])
    # Real DeepSeek output (2026-10-12): walls described as open-interest structure.
    product._validate_topic_direct_refs("这些是持仓或成交结构而非价格保证。", ["options.structure", "space.gate"])
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_topic_direct_refs("持仓24h上升。", ["contract"])


def test_breakeven_distance_from_options_vol_is_not_a_rewritten_curve():
    product._validate_financial_execution_text("盈亏平衡价按隐含波动约0.4个σ。", ["options.vol"])
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_financial_execution_text("盈亏平衡价在短腿下方。", ["contract"])


@pytest.mark.parametrize("text, refs", [
    # Independent review counterexamples: real claims hidden behind a negation cue.
    ("短腿并非安全：隐含波动下仅0.2个σ。", ["contract"]),
    ("下跌风险并非只来自巨鲸卖压与PCR偏高。", ["contract"]),
    ("风险不是来自资金费而是近端期权墙压在短腿上方。", ["contract"]),
    # options.* refs must not satisfy unrelated topics by substring.
    ("CVD持续流出，主动卖压加大。", ["options.flow"]),
    ("SRD偏斜转向Put。", ["options.vol"]),
])
def test_grounding_gate_counterexamples_still_reject(text, refs):
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_topic_direct_refs(text, refs)


def test_breakeven_exemption_only_for_sigma_distance():
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_financial_execution_text("盈亏平衡价在保护腿下方。", ["options.vol"])
    with pytest.raises(legacy.ReviewValidationError):
        product._validate_financial_execution_text("盈亏平衡价约0.5个σ，赔付曲线在保护腿下方趋平。", ["options.vol"])


def test_expired_horizon_and_unknown_side_do_not_crash_or_invent():
    expired = {u["ref"]: u for u in product._options_units(
        SNAPSHOT, CONTRACT, horizon_hours=-3.0, spot=SPOT, credit_btc=0.0034)}
    assert "short_strike_distance_in_sigma" not in expired["options.vol"]["relations"]
    call = dict(CONTRACT, side="call", short_strike_usd=84500.0, long_strike_usd=86500.0)
    snap = {**SNAPSHOT, "fact_context": {"market_facts": {"space_context": {"gex_info": dict(
        GEX, hedging_flows={"scenarios": [{"price_change_pct": 0.0, "hedging_usd": 1.0},
                                          {"price_change_pct": 1.0, "hedging_usd": -1.0}]})}}}}
    units_call = {u["ref"]: u for u in product._options_units(snap, call, horizon_hours=10, spot=SPOT, credit_btc=0.001)}
    pcts = [r["price_change_pct"] for r in units_call["options.structure"]["facts"]["dealer_hedging_adverse_scenarios"]["scenarios"]]
    assert pcts == [1.0]
    odd = dict(CONTRACT, side="straddle")
    assert all("dealer_hedging_adverse_scenarios" not in (u.get("facts") or {})
               for u in product._options_units(SNAPSHOT, odd, horizon_hours=10, spot=SPOT, credit_btc=0.001))


def test_native_check_tolerates_fields_added_to_the_projection_later():
    from tools import astra_wpem_native_v1 as native
    frozen = {"space_context": {"gex_info": {"total_net_gex": 1.0}}}
    assert native._frozen_matches(frozen, {"space_context": {"gex_info": {"total_net_gex": 1.0, "atm_iv": 29.0}}})
    assert not native._frozen_matches(frozen, {"space_context": {"gex_info": {"total_net_gex": 2.0}}})
    assert not native._frozen_matches(frozen, {"space_context": {}})
