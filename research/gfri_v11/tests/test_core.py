"""Unit tests for GFRI v1.1 core evaluator, depth gates, OKX ref, and confirmation channel."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

_WORKTREE_ROOT = str(Path(__file__).resolve().parents[3])
if _WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, _WORKTREE_ROOT)

from research.gfri_v11.config import (
    KNOWN_OI_LAG_NOTE,
    KNOWN_OI_SOURCE_LAG_HOURS,
    SCHEMA_EVAL,
    GfriConfig,
)
from research.gfri_v11.core import (
    check_deribit_instrument_unit,
    compute_directional_depth,
    compute_okx_perp_depth,
    confirm_cycle_observations,
    evaluate_single_observation,
    extract_gex_snapshot,
    validate_verified_spec_cache,
)

# Resolve shared raw_captures from sibling gfri-v1-research worktree if present
_RAW_CAPTURES_DIR = (
    Path(__file__).resolve().parents[4]
    / "gfri-v1-research"
    / "research"
    / "gfri_v1"
    / "raw_captures"
)


def _make_deribit_book(
    *,
    mid: float = 50_000.0,
    spread: float = 1.0,
    ts_ms: int = 1_795_006_275_000,
    levels_per_side: int = 25,
    step_usd: float = 25.0,
    bid_amount: float = 10_000.0,
    ask_amount: float = 10_000.0,
) -> dict:
    best_bid = mid - spread / 2.0
    best_ask = mid + spread / 2.0
    bids = [[best_bid - i * step_usd, bid_amount] for i in range(levels_per_side)]
    asks = [[best_ask + i * step_usd, ask_amount] for i in range(levels_per_side)]
    return {
        "jsonrpc": "2.0",
        "result": {
            "timestamp": ts_ms,
            "instrument_name": "BTC-PERPETUAL",
            "best_bid_price": best_bid,
            "best_ask_price": best_ask,
            "bids": bids,
            "asks": asks,
        },
    }


def _make_gex_payload(
    *,
    ts_ms: int = 1_795_006_200_000,
    computed_at_ms: int = 1_795_006_210_000,
    spot: float = 50_000.0,
    total_net_gex: float = -100_000_000.0,
    po_down_1pct: float | None = -110_000_000.0,
    po_up_1pct: float | None = 120_000_000.0,
    sl_down_1pct: float | None = -150_000_000.0,
    sl_zero_pct: float | None = 5_000_000.0,
    sl_up_1pct: float | None = 160_000_000.0,
    quality_status: str | None = "OK",
    stale: bool = False,
    zero_dte_gex: float | None = -10_000_000.0,
    snapshot_id: str = "gex:deribit:btc:snap1",
) -> dict:
    hf: dict = {
        "total_gamma": total_net_gex,
        "default_scenario": "spot_linked_12h",
        "units": {
            "total_gamma": "usd_delta_per_1pct_spot_move",
            "hedging_usd": "usd_notional",
        },
        "model": {
            "dealer_position_model": "oi_proxy_call_long_put_short",
            "dealer_positions_observed": False,
        },
        "scenarios": [
            {"price_change_pct": -1, "hedging_usd": sl_down_1pct},
            {"price_change_pct": 1, "hedging_usd": sl_up_1pct},
        ]
        if (sl_down_1pct is not None and sl_up_1pct is not None)
        else [],
        "hedging_curve": [
            {"move_pct": -1, "hedging_usd": sl_down_1pct},
            {"move_pct": 0, "hedging_usd": sl_zero_pct},
            {"move_pct": 1, "hedging_usd": sl_up_1pct},
        ]
        if sl_zero_pct is not None
        else [],
    }
    if po_down_1pct is not None or po_up_1pct is not None:
        curve = []
        if po_down_1pct is not None:
            curve.append({"move_pct": -1, "hedging_usd": po_down_1pct})
        curve.append({"move_pct": 0, "hedging_usd": 0.0})
        if po_up_1pct is not None:
            curve.append({"move_pct": 1, "hedging_usd": po_up_1pct})
        hf["price_only_curve"] = curve
        hf["scenario_sets"] = {
            "price_only": {
                "iv_mode": "fixed",
                "time_forward_hours": 0,
                "hedging_curve": curve,
            }
        }
    return {
        "data_timestamp": ts_ms,
        "computed_at": computed_at_ms,
        "asset_price": spot,
        "total_gex": total_net_gex,
        "zero_dte_gex": zero_dte_gex,
        "stale": stale,
        "snapshot_id": snapshot_id,
        "universe_hash": "hash_abc",
        "methodology_version": "deribit_coin_only_v1",
        "universe_policy": "coin_margined_only",
        "quality": {
            "status": quality_status,
            "total_open_interest": 250_000.0,
            "reasons": [],
        },
        "hedging_flows": hf,
    }


def _make_deribit_instrument(
    *,
    quote_currency: str = "USD",
    instrument_type: str = "reversed",
    contract_size: float = 10.0,
) -> dict:
    return {
        "result": {
            "instrument_name": "BTC-PERPETUAL",
            "quote_currency": quote_currency,
            "instrument_type": instrument_type,
            "contract_size": contract_size,
        }
    }


def _make_verified_spec_cache(
    *,
    verified_at_ms: int,
    expires_at_ms: int,
    schema_version: int = 1,
    quote_currency: str = "USD",
    instrument_type: str = "reversed",
    contract_size: float = 10.0,
    depth_unit: str = "usd",
) -> dict:
    return {
        "schema_version": schema_version,
        "instrument_name": "BTC-PERPETUAL",
        "quote_currency": quote_currency,
        "instrument_type": instrument_type,
        "contract_size": contract_size,
        "depth_unit": depth_unit,
        "verified_at_ms": verified_at_ms,
        "expires_at_ms": expires_at_ms,
    }


def _make_okx_instrument(*, ct_val: str = "0.01") -> dict:
    return {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "ctVal": ct_val,
                "ctValCcy": "BTC",
                "ctType": "linear",
            }
        ],
    }


def _make_okx_book(
    *,
    mid: float = 50_000.0,
    ts_ms: int = 1_795_006_275_000,
    levels: int = 25,
    step: float = 25.0,
    contracts_per_level: str = "100",
) -> dict:
    bids = [[f"{mid - 0.5 - i * step:.1f}", contracts_per_level, "0", "1"] for i in range(levels)]
    asks = [[f"{mid + 0.5 + i * step:.1f}", contracts_per_level, "0", "1"] for i in range(levels)]
    return {
        "code": "0",
        "data": [
            {
                "ts": str(ts_ms),
                "bids": bids,
                "asks": asks,
            }
        ],
    }


class TestGfriCoreV11(unittest.TestCase):
    """Comprehensive tests for GFRI v1.1 core evaluation and confirmation."""

    # --- Category A: Economics, sign convention, scaling, Decimal precision ---

    def test_category_a_negative_gamma_amplifying_both_sides(self) -> None:
        ts_gex = 1_795_006_200_000
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        gex = _make_gex_payload(
            ts_ms=ts_gex,
            computed_at_ms=ts_gex + 1000,
            total_net_gex=-100_000_000.0,
            po_down_1pct=-110_000_000.0,
            po_up_1pct=120_000_000.0,
        )
        # 25 levels * 25 USD step = 625 USD range (> 500 USD 1% band); levels within [49500, 50000] = 20 levels -> 200k USD
        ob = _make_deribit_book(mid=50_000.0, spread=1.0, ts_ms=ts_ob, levels_per_side=25, step_usd=25.0, bid_amount=10_000.0, ask_amount=10_000.0)
        res = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=_make_deribit_instrument(),
        )
        self.assertEqual(res["schema"], SCHEMA_EVAL)
        self.assertEqual(res["data_quality"], "VALID")
        self.assertTrue(res["down_1pct"]["is_amplifying"])
        self.assertTrue(res["up_1pct"]["is_amplifying"])
        self.assertGreater(res["gfri_down_1pct"], 0.0)
        self.assertGreater(res["gfri_up_1pct"], 0.0)
        self.assertEqual(res["down_1pct"]["method_tier"], "NATIVE_PRICE_ONLY")
        self.assertEqual(res["comparability"]["oi_source_lag_hours"], KNOWN_OI_SOURCE_LAG_HOURS)
        self.assertEqual(res["comparability"]["oi_lag_note"], KNOWN_OI_LAG_NOTE)
        # Segregated 0.5% research submetrics & 12h diagnostic
        self.assertIsNotNone(res["research_submetrics"]["down_0p5pct"]["gfri"])
        self.assertIsNotNone(res["research_submetrics"]["zero_dte_share"])
        self.assertEqual(res["spot_linked_12h_diagnostic"]["zero_pct_usd"], 5_000_000.0)

    def test_category_a_positive_gamma_dampening_preserves_raw_flow(self) -> None:
        ts_ob = 1_795_006_275_000
        gex = _make_gex_payload(
            ts_ms=ts_ob - 5_000,
            computed_at_ms=ts_ob - 4_000,
            total_net_gex=80_000_000.0,
            po_down_1pct=85_000_000.0,   # Dealer buys on -1% drop (dampening)
            po_up_1pct=-90_000_000.0,   # Dealer sells on +1% rally (dampening)
        )
        ob = _make_deribit_book(ts_ms=ts_ob)
        res = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=ts_ob + 100,
            deribit_instrument=_make_deribit_instrument(),
        )
        self.assertEqual(res["data_quality"], "VALID")
        self.assertEqual(res["gfri_down_1pct"], 0.0)
        self.assertEqual(res["gfri_up_1pct"], 0.0)
        self.assertFalse(res["down_1pct"]["is_amplifying"])
        self.assertFalse(res["up_1pct"]["is_amplifying"])
        self.assertEqual(res["down_1pct"]["raw_hedge_flow_usd"], 85_000_000.0)
        self.assertEqual(res["up_1pct"]["raw_hedge_flow_usd"], -90_000_000.0)
        self.assertLess(res["down_1pct"]["signed_ratio"], 0.0)
        self.assertLess(res["up_1pct"]["signed_ratio"], 0.0)

    def test_category_a_liquidity_halving_doubles_gfri(self) -> None:
        ts_ob = 1_795_006_275_000
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, computed_at_ms=ts_ob - 9_000)
        ob_full = _make_deribit_book(ts_ms=ts_ob, bid_amount=10_000.0, ask_amount=10_000.0)
        ob_half = _make_deribit_book(ts_ms=ts_ob, bid_amount=5_000.0, ask_amount=5_000.0)
        inst = _make_deribit_instrument()
        res_full = evaluate_single_observation(gex, ob_full, freeze_at_ms=ts_ob + 100, deribit_instrument=inst)
        res_half = evaluate_single_observation(gex, ob_half, freeze_at_ms=ts_ob + 100, deribit_instrument=inst)
        self.assertAlmostEqual(res_half["gfri_down_1pct"], 2.0 * res_full["gfri_down_1pct"], places=9)
        self.assertAlmostEqual(res_half["gfri_up_1pct"], 2.0 * res_full["gfri_up_1pct"], places=9)

    def test_category_a_zero_denominator_fails_closed(self) -> None:
        ts_ob = 1_795_006_275_000
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, computed_at_ms=ts_ob - 9_000)
        ob_zero = _make_deribit_book(ts_ms=ts_ob, bid_amount=0.0, ask_amount=0.0)
        res = evaluate_single_observation(
            gex,
            ob_zero,
            freeze_at_ms=ts_ob + 100,
            deribit_instrument=_make_deribit_instrument(),
        )
        self.assertEqual(res["data_quality"], "UNKNOWN")
        self.assertIsNone(res["gfri_down_1pct"])
        self.assertIsNone(res["gfri_up_1pct"])
        self.assertEqual(res["down_1pct"]["status"], "UNKNOWN")

    def test_category_a_exact_decimal_boundary_tick_included(self) -> None:
        # 50000 * 1.005 = 50250.0 exact; also check 100.1 * 1.01 = 101.101 where float64 gives 101.10100000000001
        ts_ob = 1_795_006_275_000
        bids = [[49_999.5 - i * 30.0, 1_000.0] for i in range(12)]
        asks = [[50_000.5 + i * 25.0, 1_000.0] for i in range(9)]
        # Put exact 50250.0 boundary tick and one deeper level beyond 50250.0
        asks.append([50_250.0, 7_777.0])
        asks.append([50_300.0, 5_000.0])
        ob = {
            "result": {
                "timestamp": ts_ob,
                "best_bid_price": 49_999.5,
                "best_ask_price": 50_000.5,
                "bids": bids,
                "asks": asks,
            }
        }
        depth = compute_directional_depth(ob, shock_pct=0.005, freeze_at_ms=ts_ob + 100)
        self.assertEqual(depth["up_status"], "VALID")
        self.assertEqual(depth["up_bound_usd"], 50_250.0)
        self.assertEqual(depth["down_bound_usd"], 49_750.0)
        # 9 asks inside [50000.5..50200.5] + exact 50250.0 tick = 9*1000 + 7777 = 16777.0
        self.assertEqual(depth["up_usd"], 16_777.0)

        # Float64 drift check: 100.1 * (1 + 0.01) in float64 is 101.10100000000001 > 101.101,
        # whereas exact Decimal is 101.101 so a bid/ask at 101.101 must be included!
        bids_f = [[100.05 - i * 0.2, 100.0] for i in range(11)]
        asks_f = [[100.15 + i * 0.05, 100.0] for i in range(9)] + [[101.101, 500.0], [101.20, 100.0]]
        ob_f = {
            "result": {
                "timestamp": ts_ob,
                "best_bid_price": 100.05,
                "best_ask_price": 100.15,
                "bids": bids_f,
                "asks": asks_f,
            }
        }
        depth_f = compute_directional_depth(ob_f, shock_pct=0.01, freeze_at_ms=ts_ob + 100)
        self.assertEqual(depth_f["up_usd"], 9 * 100.0 + 500.0)

    # --- Category B: Fail-closed quality, timestamps, priority, truncation, divergence ---

    def test_category_b_missing_and_future_timestamps_and_expired(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        # Missing GEX timestamp
        gex_no_ts = _make_gex_payload()
        del gex_no_ts["data_timestamp"]
        snap_missing = extract_gex_snapshot(gex_no_ts, freeze_at_ms=freeze)
        self.assertFalse(snap_missing["valid"])
        self.assertEqual(snap_missing["data_quality"], "UNKNOWN")

        # Future GEX beyond freeze + 5000ms
        gex_future = _make_gex_payload(ts_ms=freeze + 6_000, computed_at_ms=freeze + 6_000)
        snap_future = extract_gex_snapshot(gex_future, freeze_at_ms=freeze)
        self.assertFalse(snap_future["valid"])
        self.assertEqual(snap_future["data_quality"], "UNKNOWN")
        self.assertIn("gex_source_future_timestamp", snap_future["reasons"])

        # Expired GEX using freeze_at_ms as effective now (now_ms=None)
        gex_old = _make_gex_payload(ts_ms=freeze - 3_700_000, computed_at_ms=freeze - 3_690_000)
        snap_old = extract_gex_snapshot(gex_old, freeze_at_ms=freeze)
        self.assertFalse(snap_old["valid"])
        self.assertIn("gex_source_expired", snap_old["reasons"])

        # Future OB beyond freeze + 5000ms
        ob_future = _make_deribit_book(ts_ms=freeze + 6_000)
        depth_future = compute_directional_depth(ob_future, freeze_at_ms=freeze)
        self.assertEqual(depth_future["status"], "UNKNOWN")
        self.assertEqual(depth_future["reason"], "ob_future_timestamp_exceeds_freeze")

    def test_category_b_gex_quality_mapping_and_method_priority(self) -> None:
        freeze = 1_795_006_275_100
        # ERROR -> fail-closed UNKNOWN
        snap_err = extract_gex_snapshot(_make_gex_payload(ts_ms=freeze - 10_000, quality_status="ERROR"), freeze_at_ms=freeze)
        self.assertFalse(snap_err["valid"])
        self.assertEqual(snap_err["data_quality"], "UNKNOWN")

        # DEGRADED -> DEGRADED
        snap_deg = extract_gex_snapshot(_make_gex_payload(ts_ms=freeze - 10_000, quality_status="DEGRADED"), freeze_at_ms=freeze)
        self.assertTrue(snap_deg["valid"])
        self.assertEqual(snap_deg["data_quality"], "DEGRADED")

        # price_only_curve missing -> fallback ONLY to NET_GEX_LINEAR (DEGRADED), NOT spot_linked_12h!
        gex_no_po = _make_gex_payload(
            ts_ms=freeze - 10_000,
            total_net_gex=-100_000_000.0,
            po_down_1pct=None,
            po_up_1pct=None,
            sl_down_1pct=-199_000_000.0,
            sl_up_1pct=199_000_000.0,
            quality_status="OK",
        )
        snap_lin = extract_gex_snapshot(gex_no_po, freeze_at_ms=freeze)
        self.assertTrue(snap_lin["valid"])
        self.assertEqual(snap_lin["data_quality"], "DEGRADED")
        self.assertEqual(snap_lin["method_tier"], "LINEAR_PROXY_FALLBACK")
        self.assertEqual(snap_lin["primary_1pct"]["down_1pct"]["method"], "NET_GEX_LINEAR")
        self.assertEqual(snap_lin["primary_1pct"]["down_1pct"]["h_usd"], -100_000_000.0)
        self.assertEqual(snap_lin["primary_1pct"]["up_1pct"]["h_usd"], 100_000_000.0)
        # spot_linked_12h remains strictly diagnostic
        self.assertEqual(snap_lin["spot_linked_12h_diagnostic"]["down_1pct_usd"], -199_000_000.0)

        # If total_gamma unit is also unverified, must fail closed UNKNOWN even if scenarios exist!
        gex_bad_unit = copy.deepcopy(gex_no_po)
        gex_bad_unit["hedging_flows"]["units"]["total_gamma"] = "unknown_unit"
        snap_unk = extract_gex_snapshot(gex_bad_unit, freeze_at_ms=freeze)
        self.assertFalse(snap_unk["valid"])
        self.assertEqual(snap_unk["data_quality"], "UNKNOWN")

    def test_category_b_truncation_levels_divergence_spread_and_instrument(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        ob = _make_deribit_book(mid=50_000.0, ts_ms=ts_ob)

        # requested_depth=100 rejected
        d_req = compute_directional_depth(ob, requested_depth=100, freeze_at_ms=freeze)
        self.assertEqual(d_req["status"], "UNKNOWN")
        self.assertEqual(d_req["reason"], "insufficient_requested_depth")

        # < min_book_levels_per_side (9 levels) rejected
        ob_small = _make_deribit_book(mid=50_000.0, ts_ms=ts_ob, levels_per_side=9, step_usd=100.0)
        d_small = compute_directional_depth(ob_small, freeze_at_ms=freeze)
        self.assertEqual(d_small["status"], "UNKNOWN")
        self.assertEqual(d_small["reason"], "insufficient_book_levels")

        # 729 levels accepted (real Deribit depth=1000 often returns ~729 levels)
        ob_729 = _make_deribit_book(mid=50_000.0, ts_ms=ts_ob, levels_per_side=729, step_usd=2.0)
        d_729 = compute_directional_depth(ob_729, freeze_at_ms=freeze)
        self.assertEqual(d_729["status"], "VALID")

        # Truncated ask side before +1% band
        ob_trunc = _make_deribit_book(mid=50_000.0, ts_ms=ts_ob, levels_per_side=15, step_usd=10.0)
        # max ask = 50000.5 + 140 = 50140.5 < 50500, min bid = 49999.5 - 140 = 49859.5 > 49500
        d_trunc = compute_directional_depth(ob_trunc, freeze_at_ms=freeze)
        self.assertEqual(d_trunc["status"], "UNKNOWN")
        self.assertEqual(d_trunc["down_reason"], "order_book_truncated_before_band")

        # Price basis divergence > 0.2% -> DEGRADED, > 0.5% -> UNKNOWN
        d_deg = compute_directional_depth(ob, gex_spot_usd=50_000.0 * 1.0025, freeze_at_ms=freeze)
        self.assertEqual(d_deg["status"], "DEGRADED")
        self.assertEqual(d_deg["reason"], "price_basis_divergence_degraded")

        d_fail = compute_directional_depth(ob, gex_spot_usd=50_000.0 * 1.006, freeze_at_ms=freeze)
        self.assertEqual(d_fail["status"], "UNKNOWN")
        self.assertEqual(d_fail["reason"], "price_basis_divergence_exceeded")

        # Corrupted spread > 1% & crossed book
        ob_wide = _make_deribit_book(mid=50_000.0, spread=600.0, ts_ms=ts_ob)
        d_wide = compute_directional_depth(ob_wide, freeze_at_ms=freeze)
        self.assertEqual(d_wide["status"], "UNKNOWN")
        self.assertEqual(d_wide["reason"], "spread_exceeds_max_threshold")

        ob_crossed = _make_deribit_book(mid=50_000.0, spread=-5.0, ts_ms=ts_ob)
        d_cross = compute_directional_depth(ob_crossed, freeze_at_ms=freeze)
        self.assertEqual(d_cross["status"], "UNKNOWN")
        self.assertEqual(d_cross["reason"], "crossed_order_book")

        # Instrument check rejects linear or bad contract_size
        ok, unit, reason = check_deribit_instrument_unit({"result": {"quote_currency": "USDT", "instrument_type": "linear", "contract_size": 1.0}})
        self.assertFalse(ok)
        self.assertIsNone(unit)
        self.assertIn("unsupported_quote_currency", reason)

    # --- Category C: Confirmation channel & OKX cross-venue ---

    def test_category_c_sustained_stress_vs_single_blip_and_shared_gex(self) -> None:
        base_ob_ts = 1_795_006_240_000
        freeze = base_ob_ts + 45_000
        gex = _make_gex_payload(
            ts_ms=base_ob_ts - 60_000,
            computed_at_ms=base_ob_ts - 59_000,
            po_down_1pct=-300_000.0,
            po_up_1pct=150_000.0,
        )
        # Sustained halved bids -> 20 levels * 5k = 100k -> GFRI = 300k / 100k = 3.0 >= 2.5
        inst = _make_deribit_instrument()
        sustained_obs = [
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + i * 20_000, bid_amount=5_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            )
            for i in range(3)
        ]
        c_sust = confirm_cycle_observations(sustained_obs, freeze_at_ms=freeze)
        self.assertEqual(c_sust["data_quality"], "VALID")
        self.assertTrue(c_sust["shares_same_gex_snapshot"])
        self.assertEqual(c_sust["independent_gamma_estimates"], 1)
        self.assertEqual(c_sust["put"]["confirmation_status"], "CONFIRMED")
        self.assertFalse(c_sust["put"]["single_blip_detected"])
        self.assertAlmostEqual(c_sust["gfri_put_confirmed"], 3.0, places=9)
        self.assertAlmostEqual(c_sust["gfri_put_fast_max"], 3.0, places=9)

        # Single transient blip (sample 1 thin bid -> GFRI=3.0, samples 0 and 2 normal -> GFRI=1.5)
        blip_obs = [
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + 0, bid_amount=10_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + 20_000, bid_amount=5_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + 40_000, bid_amount=10_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
        ]
        c_blip = confirm_cycle_observations(blip_obs, freeze_at_ms=freeze)
        self.assertEqual(c_blip["data_quality"], "VALID")
        self.assertEqual(c_blip["put"]["confirmation_status"], "SINGLE_BLIP_FILTERED")
        self.assertTrue(c_blip["put"]["single_blip_detected"])
        self.assertAlmostEqual(c_blip["gfri_put_confirmed"], 1.5, places=9)
        self.assertAlmostEqual(c_blip["gfri_put_fast_max"], 3.0, places=9)

    def test_category_c_insufficient_samples_partial_truncation_and_window(self) -> None:
        base_ob_ts = 1_795_006_240_000
        freeze = base_ob_ts + 50_000
        gex1 = _make_gex_payload(ts_ms=base_ob_ts - 60_000, snapshot_id="snap_1")
        gex2 = _make_gex_payload(ts_ms=base_ob_ts - 30_000, snapshot_id="snap_2")
        inst = _make_deribit_instrument()

        # 1 valid observation only -> < min_confirm_valid_samples on BOTH sides -> UNKNOWN
        obs_one = [
            evaluate_single_observation(
                gex1,
                _make_deribit_book(ts_ms=base_ob_ts),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            )
        ]
        c_one = confirm_cycle_observations(obs_one, freeze_at_ms=freeze)
        self.assertEqual(c_one["data_quality"], "UNKNOWN")
        self.assertEqual(c_one["put"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")
        self.assertEqual(c_one["call"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")

        # One side confirmed (PUT) + other side truncated (CALL 0 valid) -> overall UNKNOWN
        trunc_asks_book = _make_deribit_book(ts_ms=base_ob_ts, levels_per_side=25, step_usd=25.0)
        # Keep bids full, truncate asks to 10 levels inside 50250 (< 50500)
        trunc_asks_book["result"]["asks"] = trunc_asks_book["result"]["asks"][:10]
        obs_partial = [
            evaluate_single_observation(
                gex1,
                copy.deepcopy(trunc_asks_book),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
            evaluate_single_observation(
                gex2,
                {
                    "result": {
                        **copy.deepcopy(trunc_asks_book["result"]),
                        "timestamp": base_ob_ts + 20_000,
                    }
                },
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
        ]
        c_partial = confirm_cycle_observations(obs_partial, freeze_at_ms=freeze)
        self.assertEqual(c_partial["put"]["status"], "VALID")
        self.assertEqual(c_partial["call"]["status"], "UNKNOWN")
        self.assertEqual(c_partial["data_quality"], "UNKNOWN")
        self.assertFalse(c_partial["shares_same_gex_snapshot"])
        self.assertEqual(c_partial["independent_gamma_estimates"], 2)

        # Confirm window exceeded (> 180s) -> UNKNOWN
        obs_wide = [
            evaluate_single_observation(
                gex1,
                _make_deribit_book(ts_ms=base_ob_ts),
                freeze_at_ms=freeze + 200_000,
                deribit_instrument=inst,
            ),
            evaluate_single_observation(
                gex1,
                _make_deribit_book(ts_ms=base_ob_ts + 200_000),
                freeze_at_ms=freeze + 200_000,
                deribit_instrument=inst,
            ),
        ]
        c_wide = confirm_cycle_observations(obs_wide, freeze_at_ms=freeze + 200_000)
        self.assertEqual(c_wide["data_quality"], "UNKNOWN")
        self.assertIn("confirm_window_exceeded", c_wide["reasons"])

    def test_category_c_okx_perp_depth_and_non_blocking_ref(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        okx_inst = _make_okx_instrument(ct_val="0.01")
        okx_book = _make_okx_book(mid=50_000.0, ts_ms=ts_ob, levels=25, step=25.0, contracts_per_level="100")
        okx_res = compute_okx_perp_depth(okx_book, okx_inst, shock_pct=0.01, gex_spot_usd=50_000.0, freeze_at_ms=freeze)
        self.assertEqual(okx_res["status"], "VALID")
        self.assertTrue(okx_res["non_blocking"])
        self.assertEqual(okx_res["inst_id"], "BTC-USDT-SWAP")
        # Each level: 100 contracts * 0.01 BTC * price = 1 BTC * price (~50k USD)
        self.assertGreater(okx_res["down_usd"], 900_000.0)
        self.assertGreater(okx_res["up_usd"], 900_000.0)

        # Broken OKX ref does not block Deribit primary evaluation
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000)
        ob = _make_deribit_book(ts_ms=ts_ob)
        obs = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=_make_deribit_instrument(),
            okx_book={"code": "50000", "data": []},
            okx_instrument=okx_inst,
        )
        self.assertEqual(obs["data_quality"], "VALID")
        self.assertEqual(obs["cross_venue_ref"]["status"], "UNKNOWN")
        self.assertTrue(obs["cross_venue_ref"]["non_blocking"])

    # --- Real capture integration tests ---

    def test_real_captures_snapshot_integration(self) -> None:
        gex_path = _RAW_CAPTURES_DIR / "gex_latest_20261118.json"
        ob_path = _RAW_CAPTURES_DIR / "deribit_btc_perpetual_book_depth1000_20261118.json"
        inst_path = _RAW_CAPTURES_DIR / "deribit_btc_perpetual_instrument_20261118.json"
        if not (gex_path.exists() and ob_path.exists() and inst_path.exists()):
            self.skipTest(f"Real captures directory not found: {_RAW_CAPTURES_DIR}")

        gex = json.loads(gex_path.read_text(encoding="utf-8"))
        ob = json.loads(ob_path.read_text(encoding="utf-8"))
        inst = json.loads(inst_path.read_text(encoding="utf-8"))
        freeze = int(ob["result"]["timestamp"]) + 500

        res = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        # Real capture has quality.status == "DEGRADED" (secondary venues unavailable)
        self.assertEqual(res["data_quality"], "DEGRADED")
        self.assertEqual(res["down_1pct"]["method_tier"], "NATIVE_PRICE_ONLY")
        self.assertEqual(res["up_1pct"]["method_tier"], "NATIVE_PRICE_ONLY")
        self.assertTrue(res["down_1pct"]["is_amplifying"])
        self.assertTrue(res["up_1pct"]["is_amplifying"])
        self.assertGreater(res["gfri_down_1pct"], 0.0)
        self.assertGreater(res["gfri_up_1pct"], 0.0)
        self.assertEqual(res["comparability"]["oi_source_lag_hours"], 8)
        self.assertIsNotNone(res["comparability"]["total_open_interest"])
        self.assertIsNotNone(res["spot_linked_12h_diagnostic"]["zero_pct_usd"])
        self.assertIsNotNone(res["research_submetrics"]["zero_dte_share"])
        self.assertTrue(res["provenance"]["deribit_instrument_verified"])
        self.assertIsNotNone(res["provenance"]["ob_bid_coverage_pct"])

    def test_missing_spot_fails_closed_and_two_of_three_high_is_not_ratio_blip(self) -> None:
        ts_ob = 1_795_010_000_000
        freeze = ts_ob + 100
        inst = _make_deribit_instrument()
        gex_no_spot = _make_gex_payload(ts_ms=ts_ob - 10_000)
        gex_no_spot["asset_price"] = None
        ob = _make_deribit_book(ts_ms=ts_ob)
        res_no_spot = evaluate_single_observation(gex_no_spot, ob, freeze_at_ms=freeze, deribit_instrument=inst)
        self.assertEqual(res_no_spot["data_quality"], "UNKNOWN")
        self.assertIn("missing_or_invalid_spot_usd", res_no_spot["reasons"])

        # [0.5x, 4.0x, 4.1x] has 2 sustained extreme samples (must CONFIRM, not SINGLE_BLIP_FILTERED)
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, po_down_1pct=-120_000_000.0, po_up_1pct=120_000_000.0)
        obs_low = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob - 40_000, bid_amount=12_000_000.0, ask_amount=12_000_000.0),
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        obs_h1 = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob - 20_000, bid_amount=1_500_000.0, ask_amount=1_500_000.0),
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        obs_h2 = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob, bid_amount=1_463_415.0, ask_amount=1_463_415.0),
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        confirmed = confirm_cycle_observations([obs_low, obs_h1, obs_h2], freeze_at_ms=freeze)
        self.assertEqual(confirmed["put"]["confirmation_status"], "CONFIRMED")
        self.assertFalse(confirmed["put"]["single_blip_detected"])
        self.assertGreaterEqual(confirmed["put"]["gfri_confirmed"], 3.99)

    # --- P0-1 through P0-5 regression tests ---

    def test_p0_1_deribit_instrument_and_verified_spec_cache_fail_closed(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, computed_at_ms=ts_ob - 9_000)
        ob = _make_deribit_book(ts_ms=ts_ob)

        # Both deribit_instrument and verified_spec_cache omitted -> strict fail closed
        res_missing = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=None,
            verified_spec_cache=None,
        )
        self.assertEqual(res_missing["data_quality"], "UNKNOWN")
        self.assertFalse(res_missing["provenance"]["deribit_instrument_verified"])
        self.assertFalse(res_missing["comparability"]["deribit_instrument_verified"])
        self.assertIn("missing_or_unverified_deribit_instrument", res_missing["reasons"])
        self.assertIsNone(res_missing["gfri_down_1pct"])
        self.assertIsNone(res_missing["gfri_up_1pct"])

        # Wrong contract_size (5.0) -> UNKNOWN
        res_bad_cs = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=_make_deribit_instrument(contract_size=5.0),
        )
        self.assertEqual(res_bad_cs["data_quality"], "UNKNOWN")
        self.assertFalse(res_bad_cs["provenance"]["deribit_instrument_verified"])
        self.assertIn("unsupported_contract_size", res_bad_cs["reasons"])
        self.assertIsNone(res_bad_cs["gfri_down_1pct"])
        self.assertIsNone(res_bad_cs["gfri_up_1pct"])

        # Wrong quote_currency (!= "USD") -> UNKNOWN
        res_bad_qc = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=_make_deribit_instrument(quote_currency="USDT"),
        )
        self.assertEqual(res_bad_qc["data_quality"], "UNKNOWN")
        self.assertFalse(res_bad_qc["provenance"]["deribit_instrument_verified"])
        self.assertIn("unsupported_quote_currency", res_bad_qc["reasons"])
        self.assertIsNone(res_bad_qc["gfri_down_1pct"])
        self.assertIsNone(res_bad_qc["gfri_up_1pct"])

        # Valid versioned verified_spec_cache -> passes with deribit_spec_source == "verified_cache"
        valid_cache = _make_verified_spec_cache(
            verified_at_ms=freeze - 60_000,
            expires_at_ms=freeze + 3_600_000,
        )
        ok_cache, unit_cache, err_cache = validate_verified_spec_cache(valid_cache, freeze_at_ms=freeze)
        self.assertTrue(ok_cache)
        self.assertEqual(unit_cache, "usd")
        self.assertIsNone(err_cache)

        res_cache_ok = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=None,
            verified_spec_cache=valid_cache,
        )
        self.assertEqual(res_cache_ok["data_quality"], "VALID")
        self.assertTrue(res_cache_ok["provenance"]["deribit_instrument_verified"])
        self.assertEqual(res_cache_ok["provenance"]["deribit_spec_source"], "verified_cache")
        self.assertEqual(res_cache_ok["comparability"]["deribit_spec_source"], "verified_cache")
        self.assertIsNotNone(res_cache_ok["gfri_down_1pct"])
        self.assertIsNotNone(res_cache_ok["gfri_up_1pct"])

        # Expired verified_spec_cache (expires_at_ms <= freeze_at_ms) -> fails closed
        expired_cache = _make_verified_spec_cache(
            verified_at_ms=freeze - 120_000,
            expires_at_ms=freeze,
        )
        res_cache_exp = evaluate_single_observation(
            gex,
            ob,
            freeze_at_ms=freeze,
            deribit_instrument=None,
            verified_spec_cache=expired_cache,
        )
        self.assertEqual(res_cache_exp["data_quality"], "UNKNOWN")
        self.assertFalse(res_cache_exp["provenance"]["deribit_instrument_verified"])
        self.assertIn("deribit_instrument_cache_expired", res_cache_exp["reasons"])
        self.assertIsNone(res_cache_exp["gfri_down_1pct"])
        self.assertIsNone(res_cache_exp["gfri_up_1pct"])

    def test_p0_2_partial_side_validity_call_valid_while_put_truncated(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        inst = _make_deribit_instrument()
        gex = _make_gex_payload(
            ts_ms=ts_ob - 10_000,
            computed_at_ms=ts_ob - 9_000,
            po_down_1pct=-400_000.0,
            po_up_1pct=400_000.0,
        )
        # step_usd=40: 13 ask levels inside (50000, 50500] -> 130k USD -> GFRI = 400k / 130k = 3.076923...
        # Truncate bids to 10 levels (min_bid = 49639.5 > 49500) so >= min_book_levels_per_side (10)
        # while failing -1% coverage only on PUT side.
        def _make_partial_book(ob_ts: int) -> dict:
            book = _make_deribit_book(
                mid=50_000.0,
                spread=1.0,
                ts_ms=ob_ts,
                levels_per_side=20,
                step_usd=40.0,
                bid_amount=10_000.0,
                ask_amount=10_000.0,
            )
            book["result"]["bids"] = book["result"]["bids"][:10]
            return book

        res = evaluate_single_observation(
            gex,
            _make_partial_book(ts_ob),
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        self.assertEqual(res["data_quality"], "UNKNOWN")
        self.assertEqual(res["global_data_quality"], "VALID")
        self.assertEqual(res["partial_valid_side"], "CALL")
        self.assertEqual(res["down_1pct"]["status"], "UNKNOWN")
        self.assertIsNone(res["gfri_down_1pct"])
        self.assertEqual(res["up_1pct"]["status"], "VALID")
        self.assertIsNotNone(res["gfri_up_1pct"])
        self.assertAlmostEqual(res["gfri_up_1pct"], 400_000.0 / 130_000.0, places=4)

        obs_list = [
            evaluate_single_observation(
                gex,
                _make_partial_book(ts_ob - 30_000),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
            evaluate_single_observation(
                gex,
                _make_partial_book(ts_ob - 15_000),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            ),
            res,
        ]
        confirmed = confirm_cycle_observations(obs_list, freeze_at_ms=freeze)
        self.assertEqual(confirmed["put"]["status"], "UNKNOWN")
        self.assertIsNone(confirmed["gfri_put_confirmed"])
        self.assertEqual(confirmed["call"]["status"], "VALID")
        self.assertEqual(confirmed["call"]["confirmation_status"], "CONFIRMED")
        self.assertIsNotNone(confirmed["gfri_call_confirmed"])
        self.assertAlmostEqual(confirmed["gfri_call_confirmed"], 400_000.0 / 130_000.0, places=4)

    def test_p0_3_order_book_age_and_receive_lag_gates(self) -> None:
        freeze = 1_795_006_275_100
        inst = _make_deribit_instrument()

        # GEX and OB both 20 minutes behind freeze_at_ms (ob_skew_ms == 0) -> fail closed with order_book_expired
        stale_ts = freeze - 20 * 60_000
        gex_stale = _make_gex_payload(ts_ms=stale_ts, computed_at_ms=stale_ts + 1_000)
        ob_stale = _make_deribit_book(ts_ms=stale_ts)
        res_expired = evaluate_single_observation(
            gex_stale,
            ob_stale,
            freeze_at_ms=freeze,
            deribit_instrument=inst,
        )
        self.assertEqual(res_expired["timestamps"]["ob_skew_ms"], 0)
        self.assertEqual(res_expired["data_quality"], "UNKNOWN")
        self.assertIn("order_book_expired", res_expired["reasons"])
        self.assertIsNone(res_expired["gfri_down_1pct"])
        self.assertIsNone(res_expired["gfri_up_1pct"])

        # OB exchange->HTTP receive lag ob_received_at_ms - ob_ts > max_ob_receive_lag_ms (45_000 > 30_000)
        ob_ts = freeze - 50_000
        ob_recv_ms = ob_ts + 45_000
        gex_fresh = _make_gex_payload(ts_ms=ob_ts - 5_000, computed_at_ms=ob_ts - 4_000)
        ob_lagged = _make_deribit_book(ts_ms=ob_ts)
        res_lagged = evaluate_single_observation(
            gex_fresh,
            ob_lagged,
            freeze_at_ms=freeze,
            ob_received_at_ms=ob_recv_ms,
            deribit_instrument=inst,
        )
        self.assertEqual(res_lagged["timestamps"]["ob_exchange_to_recv_ms"], 45_000)
        self.assertEqual(res_lagged["data_quality"], "UNKNOWN")
        self.assertIn("order_book_receive_lag_exceeded", res_lagged["reasons"])
        self.assertIsNone(res_lagged["gfri_down_1pct"])
        self.assertIsNone(res_lagged["gfri_up_1pct"])

    def test_p0_4_two_sample_divergence_and_confirmation_sequences(self) -> None:
        base_ob_ts = 1_795_020_000_000
        freeze = base_ob_ts + 45_000
        inst = _make_deribit_instrument()
        # 20 bid levels in [49500, 50000] -> down_usd = 20 * bid_amount
        # With po_down_1pct = -820_000.0:
        # gfri = 0.5 -> bid_amount = 82_000.0
        # gfri = 1.0 -> bid_amount = 41_000.0
        # gfri = 4.0 -> bid_amount = 10_250.0
        # gfri = 4.1 -> bid_amount = 10_000.0
        # gfri = 5.0 -> bid_amount = 8_200.0
        # gfri = 8.0 -> bid_amount = 5_125.0
        gex = _make_gex_payload(
            ts_ms=base_ob_ts - 10_000,
            computed_at_ms=base_ob_ts - 9_000,
            po_down_1pct=-820_000.0,
            po_up_1pct=820_000.0,
        )
        bid_for_gfri = {
            0.5: 82_000.0,
            1.0: 41_000.0,
            4.0: 10_250.0,
            4.1: 10_000.0,
            5.0: 8_200.0,
            8.0: 5_125.0,
        }

        def _obs(idx: int, gfri_val: float | None) -> dict:
            ts = base_ob_ts + idx * 15_000
            amt = 0.0 if gfri_val is None else bid_for_gfri[gfri_val]
            return evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=ts, bid_amount=amt, ask_amount=10_000.0),
                freeze_at_ms=freeze,
                deribit_instrument=inst,
            )

        # 1) [0.5, 8.0] (2 valid samples + 1 failed) -> INSUFFICIENT_CONFIRMATIONS + two_sample_severe_divergence
        c_div_1 = confirm_cycle_observations([_obs(0, 0.5), _obs(1, 8.0), _obs(2, None)], freeze_at_ms=freeze)
        self.assertEqual(c_div_1["put"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")
        self.assertIsNone(c_div_1["put"]["gfri_confirmed"])
        self.assertAlmostEqual(c_div_1["put"]["gfri_fast_max"], 8.0, places=9)
        self.assertIn("two_sample_severe_divergence", c_div_1["put"]["reasons"])

        # 2) [1.0, 5.0] -> INSUFFICIENT_CONFIRMATIONS, gfri_confirmed is None, gfri_fast_max == 5.0
        c_div_2 = confirm_cycle_observations([_obs(0, 1.0), _obs(1, 5.0)], freeze_at_ms=freeze)
        self.assertEqual(c_div_2["put"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")
        self.assertIsNone(c_div_2["put"]["gfri_confirmed"])
        self.assertAlmostEqual(c_div_2["put"]["gfri_fast_max"], 5.0, places=9)

        # 3) [4.0, 4.1] -> CONFIRMED, gfri_confirmed ~ 4.05, single_blip_detected is False
        c_ok_2 = confirm_cycle_observations([_obs(0, 4.0), _obs(1, 4.1)], freeze_at_ms=freeze)
        self.assertEqual(c_ok_2["put"]["confirmation_status"], "CONFIRMED")
        self.assertAlmostEqual(c_ok_2["put"]["gfri_confirmed"], 4.05, places=6)
        self.assertFalse(c_ok_2["put"]["single_blip_detected"])

        # 4) [0.5, 4.0, 4.1] -> CONFIRMED, gfri_confirmed ~ 4.0, single_blip_detected is False
        c_ok_3 = confirm_cycle_observations([_obs(0, 0.5), _obs(1, 4.0), _obs(2, 4.1)], freeze_at_ms=freeze)
        self.assertEqual(c_ok_3["put"]["confirmation_status"], "CONFIRMED")
        self.assertAlmostEqual(c_ok_3["put"]["gfri_confirmed"], 4.0, places=6)
        self.assertFalse(c_ok_3["put"]["single_blip_detected"])

    def test_p0_5_missing_price_only_metadata_rejects_native_tier(self) -> None:
        ts_ob = 1_795_006_275_000
        freeze = ts_ob + 100
        inst = _make_deribit_instrument()
        ob = _make_deribit_book(ts_ms=ts_ob)

        # Payload has price_only_curve and spot_linked_12h, but scenario_sets["price_only"] is deleted
        gex_no_meta = _make_gex_payload(
            ts_ms=ts_ob - 10_000,
            computed_at_ms=ts_ob - 9_000,
            total_net_gex=-100_000_000.0,
            po_down_1pct=-110_000_000.0,
            po_up_1pct=120_000_000.0,
            sl_down_1pct=-199_000_000.0,
            sl_up_1pct=199_000_000.0,
        )
        del gex_no_meta["hedging_flows"]["scenario_sets"]["price_only"]

        snap = extract_gex_snapshot(gex_no_meta, freeze_at_ms=freeze)
        self.assertTrue(snap["valid"])
        self.assertEqual(snap["data_quality"], "DEGRADED")
        self.assertEqual(snap["method_tier"], "LINEAR_PROXY_FALLBACK")
        self.assertIn("missing_or_invalid_price_only_metadata", snap["reasons"])
        self.assertNotEqual(snap["primary_1pct"]["down_1pct"]["method_tier"], "NATIVE_PRICE_ONLY")
        self.assertEqual(snap["primary_1pct"]["down_1pct"]["method"], "NET_GEX_LINEAR")
        self.assertEqual(snap["primary_1pct"]["down_1pct"]["h_usd"], -100_000_000.0)
        self.assertEqual(snap["primary_1pct"]["up_1pct"]["h_usd"], 100_000_000.0)

        res = evaluate_single_observation(gex_no_meta, ob, freeze_at_ms=freeze, deribit_instrument=inst)
        self.assertEqual(res["data_quality"], "DEGRADED")
        self.assertIn("missing_or_invalid_price_only_metadata", res["reasons"])
        self.assertEqual(res["down_1pct"]["method"], "NET_GEX_LINEAR")
        self.assertEqual(res["down_1pct"]["method_tier"], "LINEAR_PROXY_FALLBACK")
        self.assertEqual(res["up_1pct"]["method"], "NET_GEX_LINEAR")
        self.assertEqual(res["up_1pct"]["method_tier"], "LINEAR_PROXY_FALLBACK")
        # 100M / 200k = 500.0 (not 110M/200k=550 from price_only_curve and not 199M/200k=995 from spot_linked_12h)
        self.assertAlmostEqual(res["gfri_down_1pct"], 500.0, places=9)
        self.assertAlmostEqual(res["gfri_up_1pct"], 500.0, places=9)

        # If total_net_gex is also missing, must fail closed UNKNOWN (never NATIVE_PRICE_ONLY, never spot_linked_12h)
        gex_no_meta_no_gex = copy.deepcopy(gex_no_meta)
        gex_no_meta_no_gex["total_gex"] = None
        gex_no_meta_no_gex["hedging_flows"]["total_gamma"] = None
        res_unk = evaluate_single_observation(gex_no_meta_no_gex, ob, freeze_at_ms=freeze, deribit_instrument=inst)
        self.assertEqual(res_unk["data_quality"], "UNKNOWN")
        self.assertIn("missing_or_invalid_price_only_metadata", res_unk["reasons"])
        self.assertIsNone(res_unk["gfri_down_1pct"])
        self.assertIsNone(res_unk["gfri_up_1pct"])


if __name__ == "__main__":
    unittest.main()
