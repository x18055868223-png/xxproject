"""FMZ 2.0.4: gexmonitorapi upstream redesign adaptation.

Fields the upstream no longer provides are not consumed at all; refilled and new
upstream data reach the existing snapshot/audit/status slots; the space gate
checks only the field it uses (total_net_gex + its own source clock).
"""
import importlib.util
import pathlib

import pytest

from test_fmz_20_space_gate import BASE_MS, anchor, base_config, gamma, gex_info

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
REMOVED = ("iv_rv_ratio", "call_premium", "put_premium", "call_put_bias", "abnormal_signal")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("nrd_signal_fmz_204", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wall(strike, gex, dist):
    return {"strike": strike, "gex_usd": gex, "distance_pct": dist}


def new_layout_payload():
    return {
        "asset": "BTC", "fetched_at": None, "stale": False, "availability": "ready",
        "gex_board": {"total_net_gex": 1.5e8, "dvol": 36.9, "market_state": "positive_gamma"},
        "gamma_exposure": {"flip_point": 82724.0, "spot_price": 83000.0, "magnet_price": 80000.0,
                           "volatility_trigger": 90000.0, "n1": 78000.0, "n2": 80000.0,
                           "p1": 90000.0, "p2": 95000.0,
                           "hedging_flows": {"total_gamma_usd": 1.58e8, "scenarios": [
                               {"price_change_pct": -5, "hedging_usd": -1.86e8}]}},
        "gamma_profiles": {
            "zero_dte": {"p1": 84000.0, "p2": 84500.0, "n1": 82500.0, "n2": 81500.0,
                         "walls": {"p1": wall(84000.0, 1.25e7, 1.2), "n1": wall(82500.0, -2.4e6, -0.6)},
                         "volatility_trigger": 84000.0, "magnet_price": 83000.0},
            "weekly": {"p1": 84000.0, "p2": 85000.0, "n1": 80000.0, "n2": 78000.0,
                       "walls": {}, "volatility_trigger": 84000.0, "magnet_price": 82000.0},
        },
        "volatility": {"pcr": 0.69, "pcr_oi": 0.695, "atm_iv": 26.88, "iv_percentile": 16,
                       "term_structure": [{"expiry": "13OCT26", "days_to_expiry": 1,
                                           "atm_iv": 26.88, "skew_25d": None}],
                       "rr_skew": [{"tenor": "1d", "days": 1, "expiry": "12OCT26",
                                    "call_25d_iv": 27.1, "put_25d_iv": 29.0, "rr": -1.9,
                                    "chg_1d": 0.3, "chg_1w": -0.5, "chg_1m": 1.0,
                                    "methodology": "exact_expiry"}]},
        "flow": {"put_call_ratio": 0.69, "call_volume": 69905.5, "put_volume": 48200.2,
                 "call_oi": 457347.4, "put_oi": 318065.7,
                 "whale_1h": {"net_flow_usd": 0.0, "trade_count": 0},
                 "whale_24h": {"net_flow_usd": 390436.5, "buy_volume_usd": 579073.2,
                               "sell_volume_usd": 188636.7, "execution_net_flow_usd": -35834.4,
                               "total_volume_usd": 767709.9, "trade_count": 17, "call_count": 13,
                               "put_count": 4, "buy_call_count": 5, "sell_call_count": 8,
                               "buy_put_count": 1, "sell_put_count": 3,
                               "lead_expiry": "12OCT26", "active_strike": 84000}},
        "expiry": {"max_pain_price": 78000.0, "max_pain_target_expiry": "30OCT26",
                   "next_expiry": {"expiry": "12OCT26", "hours_to_expiry": 23.2,
                                   "put_call_ratio_oi": 0.413, "max_pain_price": 82000.0,
                                   "spot_to_max_pain_pct": -1.2}},
        "missing_fields": [],
    }


def test_version_is_204(mod):
    assert mod.CONFIG["demo_version"] == "2.0.4"


def test_removed_fields_are_not_consumed_anywhere(mod):
    snap = mod.parse_info_payload(new_layout_payload(), mod.CONFIG)
    missing = mod.missing_gex_info_snapshot(mod.CONFIG)
    spec_fields = {field for _, field, _ in mod._GEX_TIME_FIELD_SPECS}
    for name in REMOVED:
        assert name not in snap
        assert name not in missing
        assert name not in spec_fields
    table = mod._gex_info_table(snap)
    text = repr(table["rows"])
    for label in ("IV/RV", "Call权利金", "Put权利金", "Call share", "call_put_bias"):
        assert label not in text


def test_refilled_and_new_upstream_data_reach_the_snapshot(mod):
    snap = mod.parse_info_payload(new_layout_payload(), mod.CONFIG)
    assert snap["pcr"] == 0.69 and snap["put_call_ratio"] == 0.69 and snap["pcr_oi"] == 0.695
    assert snap["term_structure"][0]["days_to_expiry"] == 1
    assert snap["atm_iv"] == 26.88 and snap["iv_percentile"] == 16
    assert snap["call_volume"] == 69905.5 and snap["put_oi"] == 318065.7
    assert snap["rr_skew"][0]["rr"] == -1.9
    assert snap["whale_24h"]["net_flow_usd"] == 390436.5
    assert snap["whale_24h"]["lead_expiry"] == "12OCT26"
    assert snap["hedging_flows"]["total_gamma_usd"] == 1.58e8
    near = snap["near_term_walls"]
    assert near["zero_dte"]["resistance_walls"] == [84000.0, 84500.0]
    assert near["zero_dte"]["support_walls"] == [82500.0, 81500.0]
    assert near["weekly"]["support_walls"] == [80000.0, 78000.0]
    assert snap["max_pain_price"] == 78000.0
    assert snap["next_expiry"]["max_pain_price"] == 82000.0
    missing = mod.missing_gex_info_snapshot(mod.CONFIG)
    for key in ("pcr_oi", "atm_iv", "whale_24h", "near_term_walls", "next_expiry", "rr_skew"):
        assert key in missing


def test_old_layout_payload_still_parses_without_removed_keys(mod):
    old = {"asset": "BTC", "availability": "partial",
           "gex_board": {"total_net_gex": 1.0e8},
           "volatility": {"iv_rv_ratio": 1.1, "pcr": 0.9, "term_structure": []},
           "flow": {"call_premium": 1.0, "call_put_bias": "56% Call", "put_call_ratio": 0.9}}
    snap = mod.parse_info_payload(old, mod.CONFIG)
    assert snap["pcr"] == 0.9 and snap["near_term_walls"] == {}
    for name in REMOVED:
        assert name not in snap


def test_audit_card_exposes_near_term_walls_next_to_total_walls(mod):
    audit = mod._audit_gex_info(mod.parse_info_payload(new_layout_payload(), mod.CONFIG))
    assert audit["call_wall"] == 90000.0 and audit["put_wall"] == 80000.0
    assert audit["near_term_call_wall"] == 84000.0
    assert audit["near_term_put_wall"] == 82500.0


def test_status_table_surfaces_new_upstream_data(mod):
    text = repr(mod._gex_info_table(mod.parse_info_payload(new_layout_payload(), mod.CONFIG))["rows"])
    for label in ("0DTE", "PCR", "ATM IV", "巨鲸", "最大痛点"):
        assert label in text


def test_space_gate_uses_only_its_own_field_not_payload_availability(mod):
    cfg = base_config(mod)
    for kwargs in ({"availability": "partial"}, {"availability": "missing"},
                   {"quality": mod.QUALITY_STALE, "data_state": "lkgv_cache"}, {"stale": True}):
        snap = mod.build_space_gate_snapshot(anchor(mod), gex_info(mod, **kwargs), gamma(), cfg,
                                             observed_at_ms=BASE_MS)
        assert snap["net_gamma"]["valid"] is True, kwargs
        assert snap["state"] == mod.SPACE_GATE_ELIGIBLE, (kwargs, snap["reason_codes"])
    old_field = gex_info(mod, availability="ready", source_ts=BASE_MS - 3_600_000)
    snap = mod.build_space_gate_snapshot(anchor(mod), old_field, gamma(), cfg, observed_at_ms=BASE_MS)
    assert "SPACE_GATE_NET_GAMMA_NOT_FRESH" in snap["reason_codes"]
    assert snap["state"] == mod.SPACE_GATE_BLOCKED


def test_ggr_keeps_conservative_partial_rule_but_new_service_is_ready(mod):
    # GGR's prior design stays: a genuinely partial payload is display-only.
    # Not-provided upstream fields no longer count toward availability, so the
    # adapted service reports "ready" and GGR can use total_net_gex again.
    info = {"quality": mod.QUALITY_OK, "data_state": "live", "stale": False}
    assert mod._gex_info_usable_for_signal(dict(info, availability="ready")) is True
    assert mod._gex_info_usable_for_signal(dict(info, availability="partial")) is False


def test_space_gate_rejects_unknown_time_semantics_protocol(mod):
    info = gex_info(mod)
    info["gex_time_semantics"]["schema_version"] = "gex_time_semantics@9.0.0"
    snap = mod.build_space_gate_snapshot(anchor(mod), info, gamma(), base_config(mod),
                                         observed_at_ms=BASE_MS)
    assert snap["state"] == mod.SPACE_GATE_BLOCKED
    assert "SPACE_GATE_NET_GAMMA_SOURCE_TS_MISSING" in snap["reason_codes"]


def test_near_term_wall_records_its_bucket(mod):
    payload = new_layout_payload()
    audit = mod._audit_gex_info(mod.parse_info_payload(payload, mod.CONFIG))
    assert audit["near_term_wall_bucket"] == "zero_dte"
    del payload["gamma_profiles"]["zero_dte"]
    audit = mod._audit_gex_info(mod.parse_info_payload(payload, mod.CONFIG))
    assert audit["near_term_wall_bucket"] == "weekly"
    assert audit["near_term_call_wall"] == 84000.0 and audit["near_term_put_wall"] == 80000.0
