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


def _make_deribit_instrument() -> dict:
    return {
        "result": {
            "instrument_name": "BTC-PERPETUAL",
            "quote_currency": "USD",
            "instrument_type": "reversed",
            "contract_size": 10.0,
        }
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
        res = evaluate_single_observation(gex, ob, freeze_at_ms=ts_ob + 100)
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
        res_full = evaluate_single_observation(gex, ob_full, freeze_at_ms=ts_ob + 100)
        res_half = evaluate_single_observation(gex, ob_half, freeze_at_ms=ts_ob + 100)
        self.assertAlmostEqual(res_half["gfri_down_1pct"], 2.0 * res_full["gfri_down_1pct"], places=9)
        self.assertAlmostEqual(res_half["gfri_up_1pct"], 2.0 * res_full["gfri_up_1pct"], places=9)

    def test_category_a_zero_denominator_fails_closed(self) -> None:
        ts_ob = 1_795_006_275_000
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, computed_at_ms=ts_ob - 9_000)
        ob_zero = _make_deribit_book(ts_ms=ts_ob, bid_amount=0.0, ask_amount=0.0)
        res = evaluate_single_observation(gex, ob_zero, freeze_at_ms=ts_ob + 100)
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
        sustained_obs = [
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + i * 20_000, bid_amount=5_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
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
            ),
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + 20_000, bid_amount=5_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
            ),
            evaluate_single_observation(
                gex,
                _make_deribit_book(ts_ms=base_ob_ts + 40_000, bid_amount=10_000.0, ask_amount=15_000.0),
                freeze_at_ms=freeze,
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

        # 1 valid observation only -> < min_confirm_valid_samples on BOTH sides -> UNKNOWN
        obs_one = [evaluate_single_observation(gex1, _make_deribit_book(ts_ms=base_ob_ts), freeze_at_ms=freeze)]
        c_one = confirm_cycle_observations(obs_one, freeze_at_ms=freeze)
        self.assertEqual(c_one["data_quality"], "UNKNOWN")
        self.assertEqual(c_one["put"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")
        self.assertEqual(c_one["call"]["confirmation_status"], "INSUFFICIENT_CONFIRMATIONS")

        # One side confirmed (PUT) + other side truncated (CALL 0 valid) -> overall UNKNOWN
        trunc_asks_book = _make_deribit_book(ts_ms=base_ob_ts, levels_per_side=25, step_usd=25.0)
        # Keep bids full, truncate asks to 10 levels inside 50250 (< 50500)
        trunc_asks_book["result"]["asks"] = trunc_asks_book["result"]["asks"][:10]
        obs_partial = [
            evaluate_single_observation(gex1, copy.deepcopy(trunc_asks_book), freeze_at_ms=freeze),
            evaluate_single_observation(
                gex2,
                {
                    "result": {
                        **copy.deepcopy(trunc_asks_book["result"]),
                        "timestamp": base_ob_ts + 20_000,
                    }
                },
                freeze_at_ms=freeze,
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
            evaluate_single_observation(gex1, _make_deribit_book(ts_ms=base_ob_ts), freeze_at_ms=freeze + 200_000),
            evaluate_single_observation(gex1, _make_deribit_book(ts_ms=base_ob_ts + 200_000), freeze_at_ms=freeze + 200_000),
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
        gex_no_spot = _make_gex_payload(ts_ms=ts_ob - 10_000)
        gex_no_spot["asset_price"] = None
        ob = _make_deribit_book(ts_ms=ts_ob)
        res_no_spot = evaluate_single_observation(gex_no_spot, ob, freeze_at_ms=freeze)
        self.assertEqual(res_no_spot["data_quality"], "UNKNOWN")
        self.assertIn("missing_or_invalid_spot_usd", res_no_spot["reasons"])

        # [0.5x, 4.0x, 4.1x] has 2 sustained extreme samples (must CONFIRM, not SINGLE_BLIP_FILTERED)
        gex = _make_gex_payload(ts_ms=ts_ob - 10_000, po_down_1pct=-120_000_000.0, po_up_1pct=120_000_000.0)
        obs_low = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob - 40_000, bid_amount=12_000_000.0, ask_amount=12_000_000.0),
            freeze_at_ms=freeze,
        )
        obs_h1 = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob - 20_000, bid_amount=1_500_000.0, ask_amount=1_500_000.0),
            freeze_at_ms=freeze,
        )
        obs_h2 = evaluate_single_observation(
            gex,
            _make_deribit_book(ts_ms=ts_ob, bid_amount=1_463_415.0, ask_amount=1_463_415.0),
            freeze_at_ms=freeze,
        )
        confirmed = confirm_cycle_observations([obs_low, obs_h1, obs_h2], freeze_at_ms=freeze)
        self.assertEqual(confirmed["put"]["confirmation_status"], "CONFIRMED")
        self.assertFalse(confirmed["put"]["single_blip_detected"])
        self.assertGreaterEqual(confirmed["put"]["gfri_confirmed"], 3.99)


if __name__ == "__main__":
    unittest.main()
