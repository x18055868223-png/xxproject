"""Tests for Workstream B & C upstream provenance, clocks, health checks, and UI guardrails."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import shutil
import subprocess
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from tools import astra_case_file_v1 as case_file
from tools import astra_evidence_qual_v1 as evidence_qual
from tools import astra_expiry_gamma_v1 as expiry_gamma
from tools import astra_lock_levels_v1 as lock_levels
from tools import astra_locked_candidate_compare_v1 as compare
from tools import astra_workbench_v20 as workbench
from tools import xxproject_server_health_v1 as health_check
from test_astra_locked_candidate_compare_v1 import payload_with_components, point_from_payload
from test_astra_workbench_v20 import T, make_workbench, native_gate, write_fmz


H = expiry_gamma.HOUR_MS
EXPIRY = T + 24 * H


def _option_rows(expiry_ms: int = EXPIRY) -> list[dict]:
    code = expiry_gamma.expiry_code(expiry_ms)
    return [
        {"instrument_name": f"BTC-{code}-98000-P", "open_interest": 200.0, "mark_iv": 45.0, "underlying_price": 100_000.0},
        {"instrument_name": f"BTC-{code}-100000-P", "open_interest": 350.0, "mark_iv": 42.0, "underlying_price": 100_000.0},
        {"instrument_name": f"BTC-{code}-100000-C", "open_interest": 300.0, "mark_iv": 42.0, "underlying_price": 100_000.0},
        {"instrument_name": f"BTC-{code}-102000-C", "open_interest": 250.0, "mark_iv": 44.0, "underlying_price": 100_000.0},
    ]


def _chart_bars(now_ms: int, count: int = 180) -> dict[str, list]:
    base_tick = ((now_ms - H) // H) * H - (count - 1) * H
    ticks = [base_tick + i * H for i in range(count)]
    closes = [100_000.0 * (1.002 if i % 2 == 0 else 0.998) for i in range(count)]
    return {"ticks": ticks, "close": closes}


def _aligned_spot_window(end_ms: int, *, hours: int = 4, direction: str = "bearish") -> dict:
    start_ms = end_ms - hours * H
    return {
        "data_ready": True,
        "cvd_unit": "BTC",
        "direction": direction,
        "cvd_sum": -120.5 if direction == "bearish" else 120.5,
        "momentum_return_pct": -0.45 if direction == "bearish" else 0.45,
        "source": "binance_spot",
        "symbol": "BTCUSDT",
        "window_start_ms": start_ms,
        "end_ts_ms": end_ms,
        "available_at_ms": end_ms + 500,
        "bar_complete": True,
        "last_bar_index": 16,
        "cum_cvd_btc": -310.0,
        "cum_session_id": "sess-2026-10",
        "cum_gap_count": 0,
    }


def _row_by_key(output: dict, key: str) -> dict:
    return next(row for row in output["rows"] if row["key"] == key)


# ---------------------------------------------------------------------------
# 1. astra_expiry_gamma_v1
# ---------------------------------------------------------------------------


def test_expiry_gamma_pin_provenance_and_realized_vol_dedup_and_conflict():
    assert expiry_gamma.PIN_SOURCE == evidence_qual.PIN_SOURCE
    assert expiry_gamma.PIN_METHOD == evidence_qual.PIN_METHOD

    rows = _option_rows()
    prof = expiry_gamma.profile(rows, expiry_ms=EXPIRY, now_ms=T)
    assert prof is not None
    assert prof["pin_status"] == "ok"
    assert prof["pin_method"] == expiry_gamma.PIN_METHOD
    assert prof["pin_source"] == expiry_gamma.PIN_SOURCE

    no_pin_rows = [{"instrument_name": f"BTC-{expiry_gamma.expiry_code(EXPIRY)}-100000-C",
                    "open_interest": 0.0, "mark_iv": 40.0, "underlying_price": 100_000.0}]
    prof_unavail = expiry_gamma.profile(no_pin_rows, expiry_ms=EXPIRY, now_ms=T)
    assert prof_unavail is not None and prof_unavail["pin_status"] == "unavailable"

    chart = _chart_bars(T, count=180)
    # Identical duplicate tick with identical close deduplicates cleanly
    dup_same = {
        "ticks": [*chart["ticks"], chart["ticks"][-1]],
        "close": [*chart["close"], chart["close"][-1]],
    }
    rv_ok = expiry_gamma.realized_vol(dup_same, now_ms=T)
    assert rv_ok is not None
    assert rv_ok["status"] == "ok"
    assert rv_ok["symbol"] == "BTC-PERPETUAL"
    assert rv_ok["source"] == "deribit_public/get_tradingview_chart_data"
    assert rv_ok["bar_count_closed"] == 180
    assert rv_ok["rv_computed_at_ms"] == T
    assert rv_ok["cached_fallback"] is False
    assert rv_ok["rv_24h_window"] == {
        "start_ms": chart["ticks"][-25],
        "end_ms": chart["ticks"][-1] + H,
        "bar_count": 24,
    }
    assert rv_ok["rv_7d_window"] == {
        "start_ms": chart["ticks"][-169],
        "end_ms": chart["ticks"][-1] + H,
        "bar_count": 168,
    }

    # Conflicting duplicate tick fails closed with duplicate_conflict
    dup_conflict = {
        "ticks": [*chart["ticks"], chart["ticks"][-10]],
        "close": [*chart["close"], chart["close"][-10] + 1.0],
    }
    rv_bad = expiry_gamma.realized_vol(dup_conflict, now_ms=T)
    assert rv_bad == {
        "instrument": "BTC-PERPETUAL",
        "symbol": "BTC-PERPETUAL",
        "bar": "1h close-to-close",
        "source": "deribit_public/get_tradingview_chart_data",
        "status": "duplicate_conflict",
        "last_bar_end_ms": None,
        "rv_24h_pct": None,
        "rv_7d_pct": None,
        "rv_computed_at_ms": T,
        "cached_fallback": False,
    }
    assert evidence_qual.qualify_expiry_realized_vol(rv_bad, cutoff_ms=T)["status"] == evidence_qual.QUAL_UNKNOWN

    # Insufficient bars
    short_chart = _chart_bars(T, count=10)
    rv_short = expiry_gamma.realized_vol(short_chart, now_ms=T)
    assert rv_short["status"] == "insufficient_bars"
    assert rv_short["rv_24h_window"] is None and rv_short["rv_7d_window"] is None


def test_expiry_gamma_collect_clock_due_retry_and_for_expiry_provenance():
    rows = _option_rows()
    chart = _chart_bars(T, count=180)

    def ok_get(url, params):
        if "book_summary" in url:
            return {"result": rows}
        return {"result": chart}

    res = expiry_gamma.collect(ok_get, T, clock_ms=lambda: T + 321)
    assert res["available_at_ms"] == T + 321
    assert res["realized_vol"]["rv_available_at_ms"] == T + 321
    assert res["realized_vol"]["rv_computed_at_ms"] == T

    pin_rec = expiry_gamma.for_expiry(res, EXPIRY, now_ms=T + 1_000)
    assert pin_rec["available_at_ms"] == T + 321
    assert pin_rec["pin_status"] == "ok"
    assert pin_rec["pin_method"] == expiry_gamma.PIN_METHOD
    assert pin_rec["pin_source"] == expiry_gamma.PIN_SOURCE
    assert "last_attempt" in pin_rec
    assert evidence_qual.qualify_live_pin(pin_rec, expiry_ms=EXPIRY, cutoff_ms=T + 1_000)["status"] == evidence_qual.QUAL_ALIGNED
    assert evidence_qual.qualify_expiry_realized_vol(res["realized_vol"], cutoff_ms=T + 1_000)["status"] == evidence_qual.QUAL_ALIGNED

    # Chart failure sets full fetch_failed provenance and retries at RETRY_MS (1m)
    def chart_fail(url, params):
        if "book_summary" in url:
            return {"result": rows}
        raise TimeoutError("chart timeout")

    failed_rv = expiry_gamma.collect(chart_fail, T, clock_ms=lambda: T + 100)
    assert failed_rv["status"] == "ok"
    assert failed_rv["available_at_ms"] == T + 100
    assert failed_rv["realized_vol"] == {
        "status": "fetch_failed",
        "error_type": "TimeoutError",
        "instrument": "BTC-PERPETUAL",
        "symbol": "BTC-PERPETUAL",
        "bar": "1h close-to-close",
        "source": "deribit_public/get_tradingview_chart_data",
        "rv_computed_at_ms": None,
        "rv_available_at_ms": None,
        "cached_fallback": False,
    }
    assert not expiry_gamma.due(failed_rv, T + 30_000)
    assert expiry_gamma.due(failed_rv, T + expiry_gamma.RETRY_MS)
    assert expiry_gamma.due({**res, "realized_vol": {**res["realized_vol"], "cached_fallback": True}}, T + expiry_gamma.RETRY_MS)

    # Legacy result without available_at_ms falls back to computed_at_ms, but fails qualify_live_pin if pin_method missing
    legacy_res = {
        "schema": expiry_gamma.SCHEMA,
        "status": "ok",
        "computed_at_ms": T,
        "expiries": {expiry_gamma.expiry_code(EXPIRY): {"expiry": expiry_gamma.expiry_code(EXPIRY), "expiry_ms": EXPIRY, "walls": {"pin": 100_000.0}}},
    }
    legacy_prof = expiry_gamma.for_expiry(legacy_res, EXPIRY, now_ms=T + 500)
    assert legacy_prof["available_at_ms"] == T
    assert legacy_prof["pin_method"] is None
    assert evidence_qual.qualify_live_pin(legacy_prof, expiry_ms=EXPIRY, cutoff_ms=T + 500)["status"] == evidence_qual.QUAL_UNALIGNED


# ---------------------------------------------------------------------------
# 2. astra_workbench_v20
# ---------------------------------------------------------------------------


def test_workbench_refresh_expiry_gamma_fallback_and_strict_lookahead(tmp_path, monkeypatch):
    original, snapshots = make_workbench(tmp_path)
    clock = [T]
    wb = workbench.AstraWorkbench(
        replace(original.config, expiry_gamma_enabled=True),
        discovery_capture=original.discovery_capture,
        quote_capture=original.quote_capture,
        clock_ms=lambda: clock[0],
    )
    write_fmz(snapshots, native_gate())

    first_cand = next(iter(original.discovery_capture(now_ms=T)["candidates"].values()))
    snap_expiry = first_cand.get("expiry_ms") or EXPIRY
    rows = _option_rows(snap_expiry)
    chart = _chart_bars(T, count=180)
    fail_chart = [False]

    def fake_get_json(url, params):
        if "book_summary" in url:
            return {"result": rows}
        if fail_chart[0]:
            raise RuntimeError("chart down")
        return {"result": chart}

    wb.expiry_gamma_get_json = fake_get_json
    first = wb.refresh_expiry_gamma(now_ms=T, force=True)
    orig_rv_comp = first["realized_vol"]["rv_computed_at_ms"]
    orig_rv_avail = first["realized_vol"]["rv_available_at_ms"]
    orig_last_bar = first["realized_vol"]["last_bar_end_ms"]
    assert orig_rv_comp == T

    # Advance 2 minutes and fail chart: old_rv clocks preserved, cached_fallback=True stamped
    clock[0] = T + 120_000
    fail_chart[0] = True
    second = wb.refresh_expiry_gamma(now_ms=clock[0], force=True)
    rv2 = second["realized_vol"]
    assert rv2["cached_fallback"] is True
    assert rv2["rv_computed_at_ms"] == orig_rv_comp
    assert rv2["rv_available_at_ms"] == orig_rv_avail
    assert rv2["last_bar_end_ms"] == orig_last_bar
    assert rv2["last_attempt"] == "fetch_failed"
    assert rv2["last_attempt_at_ms"] == clock[0]
    assert rv2["last_attempt_error_type"] == "RuntimeError"
    assert evidence_qual.qualify_expiry_realized_vol(rv2, cutoff_ms=clock[0])["capability"] == evidence_qual.CAP_BLIND_STALE
    # Retry due at RETRY_MS (1m) because cached_fallback=True
    assert expiry_gamma.due(second, clock[0] + expiry_gamma.RETRY_MS)

    # Strict anti-lookahead guard in _with_expiry_levels: computed_at_ms > at is excluded (even +1 ms)
    wb_no_refresh = workbench.AstraWorkbench(
        replace(original.config, expiry_gamma_enabled=False),
        discovery_capture=original.discovery_capture,
        quote_capture=original.quote_capture,
        clock_ms=lambda: T,
    )
    code = expiry_gamma.expiry_code(snap_expiry)
    future_profile = expiry_gamma.profile(rows, expiry_ms=snap_expiry, now_ms=T)
    wb_no_refresh._expiry_gamma = {
        "schema": expiry_gamma.SCHEMA,
        "status": "ok",
        "computed_at_ms": T + 1,
        "available_at_ms": T + 1,
        "expiries": {code: future_profile},
    }
    pt = point_from_payload(payload_with_components(), point_id="p_lookahead", recorded_at_ms=T)
    pt["candidate"]["snapshot"]["contract"]["expiry_ms"] = snap_expiry
    with_levels = wb_no_refresh._with_expiry_levels(pt)
    assert with_levels["candidate"]["expiry_levels"]["profile_present"] is False
    assert with_levels["candidate"]["expiry_levels"]["pin"] is None

    # Backwards-compat when tests mock expiry_gamma.collect with 2 positional args
    seen_calls = []
    monkeypatch.setattr(
        workbench.expiry_gamma,
        "collect",
        lambda get_json, now_ms: seen_calls.append(now_ms) or {"schema": expiry_gamma.SCHEMA, "status": "ok", "computed_at_ms": now_ms, "expiries": {}},
    )
    compat_res = wb.refresh_expiry_gamma(now_ms=T + 300_000, force=True)
    assert seen_calls == [T + 300_000]
    assert compat_res["status"] == "ok"


# ---------------------------------------------------------------------------
# 3. astra_case_file_v1
# ---------------------------------------------------------------------------


def test_case_file_l4_expiry_vol_lineage_clocks_quality_and_legacy_annotate():
    snap = {
        "snapshot_id": "a" * 64,
        "candidate_id": "c" * 24,
        "contract": {
            "side": "put",
            "venue": "Deribit",
            "underlying": "BTC",
            "settlement_currency": "BTC",
            "short_instrument": "BTC-20OCT26-82000-P",
            "long_instrument": "BTC-20OCT26-80000-P",
            "short_strike_usd": 82000.0,
            "long_strike_usd": 80000.0,
            "width_usd": 2000.0,
            "quantity_btc": 1.0,
            "expiry_ms": EXPIRY,
        },
        "liability": {"payout_table": [], "breakeven_usd": [81800.0]},
        "economics": {"net_credit_after_fee_btc": 0.002, "valuation_spot_usd": 83000.0},
        "market": {"valuation_ts_ms": T, "quote": {"gap_reasons": []}},
        "fact_context": {"status": "current", "snapshot_ts_ms": T, "market_facts": {}},
        "data_identity": {"btc_map": {}},
    }
    raw_market = {
        "short_book": {"underlying_price": 83000.0, "mark_price": 0.003, "mark_iv": 40.0, "greeks": {"delta": -0.3}},
        "long_book": {"underlying_price": 83000.0, "mark_price": 0.001, "mark_iv": 45.0, "greeks": {"delta": -0.1}},
    }
    code = expiry_gamma.expiry_code(EXPIRY)
    good_view = {
        "expiry_gamma": {
            "by_expiry": {
                code: {
                    "expiry": code,
                    "expiry_ms": EXPIRY,
                    "atm_iv_pct": 42.0,
                    "expected_move_usd": 1900.0,
                    "forward_usd": 83010.0,
                    "computed_at_ms": T - 30_000,
                    "available_at_ms": T - 29_500,
                    "stale": False,
                }
            },
            "realized_vol": {
                "instrument": "BTC-PERPETUAL",
                "symbol": "BTC-PERPETUAL",
                "bar": "1h close-to-close",
                "source": "deribit_public/get_tradingview_chart_data",
                "status": "ok",
                "rv_24h_pct": 38.0,
                "rv_7d_pct": 40.0,
                "last_bar_end_ms": T - 60_000,
                "rv_computed_at_ms": T - 20_000,
                "rv_available_at_ms": T - 19_500,
                "cached_fallback": False,
            },
        }
    }
    case_ok = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, view=good_view)
    ev = case_ok["layers"]["L4"][case_ok["refs"]["L4.expiry_vol"]["index"]]
    assert ev["lineage"] == ["deribit.book_summary", "deribit.perpetual_chart"]
    assert "deribit.index" not in ev["lineage"]
    assert ev["asof_ms"] == T - 20_000
    assert ev["quality"] == "ok"
    assert ev["values"]["expiry_ms"] == EXPIRY
    assert ev["values"]["iv_asof_ms"] == T - 30_000
    assert ev["values"]["iv_available_at_ms"] == T - 29_500
    assert ev["values"]["iv_stale"] is False

    # Degraded quality cases
    cached_view = deepcopy(good_view)
    cached_view["expiry_gamma"]["realized_vol"]["cached_fallback"] = True
    ev_cached = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, view=cached_view)
    ev_c = ev_cached["layers"]["L4"][ev_cached["refs"]["L4.expiry_vol"]["index"]]
    assert ev_c["quality"] == "stale" and ev_c["quality_reason"] == "rv_cached_fallback"

    conflict_view = deepcopy(good_view)
    conflict_view["expiry_gamma"]["realized_vol"]["status"] = "duplicate_conflict"
    ev_conf = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, built_at_ms=T, view=conflict_view)
    ev_d = ev_conf["layers"]["L4"][ev_conf["refs"]["L4.expiry_vol"]["index"]]
    assert ev_d["quality"] == "unknown" and ev_d["quality_reason"] == "rv_duplicate_conflict"

    stale_iv_view = deepcopy(good_view)
    stale_iv_view["expiry_gamma"]["by_expiry"][code]["stale"] = True
    ev_siv = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, built_at_ms=T, view=stale_iv_view)
    ev_s = ev_siv["layers"]["L4"][ev_siv["refs"]["L4.expiry_vol"]["index"]]
    assert ev_s["quality"] == "stale" and ev_s["quality_reason"] == "expiry_iv_stale"

    only_failed_rv = {"expiry_gamma": {"by_expiry": {}, "realized_vol": {"status": "fetch_failed"}}}
    ev_fail = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, built_at_ms=T, view=only_failed_rv)
    ev_f = ev_fail["layers"]["L4"][ev_fail["refs"]["L4.expiry_vol"]["index"]]
    assert ev_f["quality"] == "unknown" and ev_f["quality_reason"] == "rv_fetch_failed"

    # Consumer-chained proof: case_file -> level_behaviour.build_card -> player_input.build_input (p0_strict@1)
    from tools import astra_level_behaviour_v1 as lb
    from tools import astra_player_input_v1 as player_input

    def _pair_for_case(put_case_in):
        put_c = deepcopy(put_case_in)
        cutoff = put_c["build"]["built_at_ms"]
        call_snap = deepcopy(snap)
        call_snap["contract"]["side"] = "call"
        call_snap["contract"]["short_instrument"] = "BTC-20OCT26-84000-C"
        call_snap["contract"]["long_instrument"] = "BTC-20OCT26-86000-C"
        call_snap["contract"]["short_strike_usd"] = 84000.0
        call_snap["contract"]["long_strike_usd"] = 86000.0
        call_c = case_file.build(snapshot=call_snap, raw_market=raw_market, asof_ms=cutoff, built_at_ms=cutoff, view=good_view)
        idx_pts = [[cutoff - 120_000, 83000.0], [cutoff - 60_000, 83000.0]]
        put_card = lb.build_card(
            expiry_ms=EXPIRY, rnd="r16", side="put", read_at_ms=cutoff,
            walls={"pin": 83000.0}, points=idx_pts, case=put_c,
            contract=put_c["layers"]["L1"][put_c["refs"]["L1.contract"]["index"]]["values"],
        )
        call_card = lb.build_card(
            expiry_ms=EXPIRY, rnd="r16", side="call", read_at_ms=cutoff,
            walls={"pin": 83000.0}, points=idx_pts, case=call_c,
            contract=call_c["layers"]["L1"][call_c["refs"]["L1.contract"]["index"]]["values"],
        )
        return put_c, call_c, put_card, call_card

    idx_path = {"index_name": "btc_usd", "expiry_ms": EXPIRY, "timestamp_semantics": "minute_close", "points": [[T - 120_000, 83000.0], [T - 60_000, 83000.0]]}
    # 1) duplicate_conflict -> RV24为UNKNOWN (NOT STALE)
    p_c, c_c, p_cd, c_cd = _pair_for_case(ev_conf)
    md_conf = player_input.build_input(p_c, c_c, p_cd, c_cd, index_path=idx_path, include_ard=False, input_profile=player_input.INPUT_PROFILE_P0_STRICT)
    assert "历史实现波动率RV24为UNKNOWN" in md_conf and "RV24为STALE" not in md_conf

    # 2) fetch_failed (even with expiry_row present) -> RV24为UNKNOWN (NOT STALE)
    fetch_with_exp_view = deepcopy(good_view)
    fetch_with_exp_view["expiry_gamma"]["realized_vol"]["status"] = "fetch_failed"
    ev_fwe = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, built_at_ms=T, view=fetch_with_exp_view)
    p_c2, c_c2, p_cd2, c_cd2 = _pair_for_case(ev_fwe)
    md_fwe = player_input.build_input(p_c2, c_c2, p_cd2, c_cd2, index_path=idx_path, include_ard=False, input_profile=player_input.INPUT_PROFILE_P0_STRICT)
    assert "历史实现波动率RV24为UNKNOWN" in md_fwe and "RV24为STALE" not in md_fwe

    # 3) expiry_iv_stale ONLY does NOT blind healthy fresh RV24 (verify both _format_vol_background_p0 directly and L4 entry passed through build_input)
    p_c3, c_c3, p_cd3, c_cd3 = _pair_for_case(ev_conf)
    l4_idx_p3 = p_c3["refs"]["L4.expiry_vol"]["index"]
    p_c3["layers"]["L4"][l4_idx_p3] = deepcopy(ev_s)
    md_siv = player_input.build_input(p_c3, c_c3, p_cd3, c_cd3, index_path=idx_path, include_ard=False, input_profile=player_input.INPUT_PROFILE_P0_STRICT)
    assert "RV24 38.00%" in md_siv
    vol_siv = player_input._format_vol_background_p0(
        {"put": ev_siv, "call": c_c3}, {"put": p_cd3, "call": c_cd3},
        {"put": p_c3["layers"]["L1"][p_c3["refs"]["L1.contract"]["index"]]["values"],
         "call": c_c3["layers"]["L1"][c_c3["refs"]["L1.contract"]["index"]]["values"]},
        T,
    )
    assert "RV24 38.00%" in vol_siv

    # 4) rv_cached_fallback -> RV24为STALE
    ev_cached_b = case_file.build(snapshot=snap, raw_market=raw_market, asof_ms=T, built_at_ms=T, view=cached_view)
    p_c4, c_c4, p_cd4, c_cd4 = _pair_for_case(ev_cached_b)
    md_cached = player_input.build_input(p_c4, c_c4, p_cd4, c_cd4, index_path=idx_path, include_ard=False, input_profile=player_input.INPUT_PROFILE_P0_STRICT)
    assert "历史实现波动率RV24为STALE" in md_cached

    # Legacy annotate() compatibility with asof_ms=None
    legacy = deepcopy(case_ok)
    legacy["layers"]["L4"][legacy["refs"]["L4.expiry_vol"]["index"]]["asof_ms"] = None
    legacy["layers"]["L4"][legacy["refs"]["L4.expiry_vol"]["index"]]["lineage"] = ["deribit.book_summary", "deribit.index"]
    annotated = case_file.annotate(legacy)
    assert annotated["layers"]["L4"][annotated["refs"]["L4.expiry_vol"]["index"]]["asof_ms"] is None


# ---------------------------------------------------------------------------
# 4. astra_locked_candidate_compare_v1
# ---------------------------------------------------------------------------


def test_locked_compare_flow_qualification_and_stale_levels():
    assert compare.SERIES_METRICS_VERSION == 5

    # Conflicting end clocks are rejected by _flow_asof_ms
    assert compare._flow_asof_ms({"end_ts_ms": T - 1000, "complete_ts_ms": T - 2000}) is None
    assert compare._flow_asof_ms({"end_ts_ms": T - 1000, "complete_ts_ms": T - 1000}) == T - 1000

    base = point_from_payload(payload_with_components(), point_id="t0", recorded_at_ms=T + 1)
    latest = point_from_payload(payload_with_components(), point_id="t1", recorded_at_ms=T + 60_000)

    # Qualified fast_4h + slow_12h populates flow_window_basis and ready flags
    aligned_fast = _aligned_spot_window(T - 5_000, hours=4)
    aligned_slow = _aligned_spot_window(T - 5_000, hours=12)
    mf_good = {
        "volume_price": {
            "asof_ms": T,
            "tmv_state": "bearish_bias",
            "micro_flow": {
                "fast_4h": aligned_fast,
                "slow_12h": aligned_slow,
                "combined": {"direction": "bearish", "data_ready": True},
            },
        }
    }
    latest["candidate"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(mf_good)
    latest["packet"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(mf_good)
    metrics_ok = compare.point_metrics(latest, base)
    assert metrics_ok["flow_fast_4h_ready"] is True
    assert metrics_ok["flow_slow_12h_ready"] is True
    assert metrics_ok["flow_qualification_status"] == evidence_qual.QUAL_ALIGNED
    assert metrics_ok["flow_qualification_reasons"] == []
    assert metrics_ok["flow_asof_ms"] == T - 5_000
    assert metrics_ok["flow_window_basis"]["source"] == "binance_spot"
    assert metrics_ok["flow_window_basis"]["symbol"] == "BTCUSDT"
    assert metrics_ok["flow_window_basis"]["start_ms"] == T - 5_000 - 4 * H
    assert metrics_ok["flow_window_basis"]["end_ms"] == T - 5_000
    assert metrics_ok["flow_window_basis"]["bar_complete"] is True
    assert metrics_ok["flow_window_basis"]["last_bar_index"] == 16
    assert metrics_ok["flow_window_basis"]["cum_cvd_btc"] == -310.0

    # Broken provenance (data_ready=True but missing source/symbol/window_start_ms) -> flow_window_basis=None
    broken = deepcopy(mf_good)
    broken["volume_price"]["micro_flow"]["fast_4h"].pop("source")
    broken["volume_price"]["micro_flow"]["fast_4h"].pop("symbol")
    broken["volume_price"]["micro_flow"]["fast_4h"].pop("window_start_ms")
    latest["candidate"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(broken)
    latest["packet"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(broken)
    metrics_bad = compare.point_metrics(latest, base)
    assert metrics_bad["flow_fast_4h_ready"] is False
    assert metrics_bad["flow_slow_12h_ready"] is True
    assert metrics_bad["flow_qualification_status"] == evidence_qual.QUAL_UNALIGNED
    assert "missing_source" in metrics_bad["flow_qualification_reasons"]
    assert metrics_bad["flow_asof_ms"] is None
    assert metrics_bad["flow_window_basis"] is None

    # Degraded flag on extra_scopes (volume_price.cvd_degraded=True) and future clock exclusion
    degraded = deepcopy(mf_good)
    degraded["volume_price"]["cvd_degraded"] = True
    latest["candidate"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(degraded)
    latest["packet"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(degraded)
    metrics_deg = compare.point_metrics(latest, base)
    assert metrics_deg["flow_fast_4h_ready"] is False
    assert metrics_deg["flow_window_basis"] is None
    assert "degraded:cvd_degraded" in metrics_deg["flow_qualification_reasons"]

    future = deepcopy(mf_good)
    future["volume_price"]["micro_flow"]["fast_4h"] = _aligned_spot_window(T + 120_000, hours=4)
    latest["candidate"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(future)
    latest["packet"]["snapshot"]["fact_context"]["market_facts"] = deepcopy(future)
    metrics_fut = compare.point_metrics(latest, base)
    assert metrics_fut["flow_fast_4h_ready"] is False
    assert metrics_fut["flow_qualification_status"] == evidence_qual.QUAL_UNKNOWN
    assert metrics_fut["flow_qualification_reasons"] == ["future_clock_excluded"]
    assert metrics_fut["flow_window_basis"] is None

    # Stale level status in _change_cn and _level_rows
    meta = {k: "same" for k in ("semantic_key", "method", "product", "source", "window", "unit")}
    txt, st = compare._change_cn(
        {"value": 100_000.0, "asof_ms": T - 120_000, "meta": meta},
        {"value": 100_000.0, "asof_ms": T - 60_000, "meta": meta, "stale": True},
    )
    assert (txt, st) == ("最近读数偏旧，不判未变。", "stale")

    t0_prof = expiry_gamma.profile(_option_rows(), expiry_ms=EXPIRY, now_ms=T)
    t0_prof["computed_at_ms"] = T
    t0_prof["available_at_ms"] = T
    t1_prof = expiry_gamma.profile(_option_rows(), expiry_ms=EXPIRY, now_ms=T + 1_000)
    t1_prof["computed_at_ms"] = T
    t1_prof["available_at_ms"] = T
    base["candidate"]["expiry_levels"] = lock_levels.compact(t0_prof, {}, {}, now_ms=T, expiry_ms=EXPIRY)
    latest["candidate"]["expiry_levels"] = lock_levels.compact(
        t1_prof, {}, {}, now_ms=T + lock_levels.STALE_MS + 10_000, expiry_ms=EXPIRY
    )
    assert latest["candidate"]["expiry_levels"]["stale"] is True
    followup = compare.build_followup({"episode": {"id": "ep"}, "baseline": base, "latest": latest, "observations": []}, now_ms=T + lock_levels.STALE_MS + 10_000)
    pin_row = _row_by_key(followup, "levels.pin")
    assert pin_row["status"] == "stale"
    assert pin_row["change_cn"] == "最近读数偏旧，不判未变。"


# ---------------------------------------------------------------------------
# 5. xxproject_server_health_v1
# ---------------------------------------------------------------------------


def test_server_health_evidence_channels_and_warming_vs_broken():
    import inspect

    sig = inspect.signature(health_check.run_health_check)
    assert sig.parameters["expected_core_version"].default == "2.0.7"

    now_ms = T
    code = expiry_gamma.expiry_code(EXPIRY)
    qualified_view = {
        "served_at_ms": now_ms,
        "updated_at_ms": now_ms - 1_000,
        "market_context": {
            "status": "current",
            "strategy_version": "2.0.7",
            "market_facts": {
                "volume_price": {
                    "micro_flow": {
                        "fast_4h": _aligned_spot_window(now_ms - 10_000, hours=4),
                    }
                }
            },
        },
        "discovery": {"candidates": {"c1": {"execution_allowed": False}}},
        "candidates": [{"snapshot": {"contract": {"expiry_ms": EXPIRY}}}],
        "expiry_gamma": {
            "status": "ok",
            "by_expiry": {
                code: {
                    "expiry": code,
                    "expiry_ms": EXPIRY,
                    "pin_status": "ok",
                    "pin_method": expiry_gamma.PIN_METHOD,
                    "pin_source": expiry_gamma.PIN_SOURCE,
                    "computed_at_ms": now_ms - 30_000,
                    "available_at_ms": now_ms - 29_500,
                    "stale": False,
                    "walls": {"pin": 100_000.0},
                }
            },
            "realized_vol": {
                "instrument": "BTC-PERPETUAL",
                "symbol": "BTC-PERPETUAL",
                "bar": "1h close-to-close",
                "source": "deribit_public/get_tradingview_chart_data",
                "status": "ok",
                "rv_24h_pct": 35.0,
                "last_bar_end_ms": now_ms - 300_000,
                "rv_computed_at_ms": now_ms - 30_000,
                "rv_available_at_ms": now_ms - 29_500,
                "cached_fallback": False,
            },
        },
    }

    def mk_http(payload):
        return lambda url, **_kw: (200, {"ok": True}) if "session" in url else (200, payload)

    res_ok = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.7",
        http_get=mk_http(qualified_view),
        now_ms=now_ms,
        require_evidence_channels=True,
    )
    assert res_ok["ok"] is True
    assert res_ok["evidence_contract_ok"] is True
    assert res_ok["blind_channels"] == []
    assert res_ok["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_ACTIVE
    assert res_ok["evidence_channels"]["pin_structure"]["capability"] == evidence_qual.CAP_ACTIVE
    assert res_ok["evidence_channels"]["rv24_background"]["capability"] == evidence_qual.CAP_ACTIVE

    # Broken producer provenance: data_ready=True but missing source/symbol/window_start_ms
    broken_view = deepcopy(qualified_view)
    fast_bad = broken_view["market_context"]["market_facts"]["volume_price"]["micro_flow"]["fast_4h"]
    fast_bad.pop("source")
    fast_bad.pop("symbol")
    res_broken = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.7",
        http_get=mk_http(broken_view),
        now_ms=now_ms,
        require_evidence_channels=True,
    )
    assert res_broken["ok"] is False
    assert res_broken["reason"] == "workbench_evidence_contract_broken"
    assert res_broken["evidence_contract_ok"] is False
    assert "spot_flow" in res_broken["blind_channels"]
    assert res_broken["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_BLIND_UNALIGNED

    # Cold-start WARMING_UP: data_ready=False is NOT broken provenance and NOT in blind_channels
    warming_view = deepcopy(qualified_view)
    warming_view["market_context"]["market_facts"]["volume_price"]["micro_flow"]["fast_4h"] = {"data_ready": False}
    res_warm = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.7",
        http_get=mk_http(warming_view),
        now_ms=now_ms,
        require_evidence_channels=True,
    )
    assert res_warm["ok"] is True
    assert res_warm["evidence_contract_ok"] is True
    assert res_warm["blind_channels"] == []
    assert res_warm["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_WARMING_UP

    # Post-restart kline backfill (data_ready=True + window_backfill=True with full provenance):
    # health check reports WARMING_UP (NOT workbench_evidence_contract_broken!)
    backfill_view = deepcopy(qualified_view)
    bf_fast = backfill_view["market_context"]["market_facts"]["volume_price"]["micro_flow"]["fast_4h"]
    bf_fast["window_backfill"] = True
    res_bf = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.7",
        http_get=mk_http(backfill_view),
        now_ms=now_ms,
        require_evidence_channels=True,
    )
    assert res_bf["ok"] is True
    assert res_bf["evidence_contract_ok"] is True
    assert res_bf["blind_channels"] == []
    assert res_bf["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_WARMING_UP

    # Real FMZ 2.0.7 post-restart kline backfill + live aggTrade transition (backfill_mixed=True):
    # MUST report CAP_WARMING_UP (NOT workbench_evidence_contract_broken during the first 4h live warm-up!)
    import importlib.util as _iu
    from test_p5_flow_e2e_chain import _live_assembler, _klines, _feed_live_bars, MIN as FLOW_MIN

    fmz_spec = _iu.spec_from_file_location("nrd_signal_fmz_health_mix", ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py")
    assert fmz_spec is not None and fmz_spec.loader is not None
    fmz_mod = _iu.module_from_spec(fmz_spec)
    fmz_spec.loader.exec_module(fmz_mod)
    fmz_mod.fmz_log = lambda *parts: None
    fmz_mod.fmz_status = lambda text: None
    fmz_mod.fmz_push = lambda text: None
    fmz_mod.now_ms = lambda: now_ms

    assembler, cfg = _live_assembler(fmz_mod)
    assert assembler.backfill_from_klines(
        _klines(12 * 60, end_ms=now_ms - 30 * FLOW_MIN, volume=12.0, taker_buy=7.0),
        now_ms,
    )["applied"] is True
    _feed_live_bars(assembler, start_ts_ms=now_ms - 20 * FLOW_MIN, bar_count=2, step_ms=10 * FLOW_MIN)
    mixed_ctx = fmz_mod.compute_micro_flow_context(list(assembler.completed_bars), cfg, as_of_ms=now_ms)
    mixed_fast = dict(mixed_ctx["fast_4h"])
    assert mixed_fast["window_backfill"] is True and mixed_fast["backfill_mixed"] is True

    mixed_view = deepcopy(qualified_view)
    mixed_view["market_context"]["market_facts"]["volume_price"]["micro_flow"]["fast_4h"] = mixed_fast
    res_mixed = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.7",
        http_get=mk_http(mixed_view),
        now_ms=now_ms,
        require_evidence_channels=True,
    )
    assert res_mixed["ok"] is True
    assert res_mixed["evidence_contract_ok"] is True
    assert res_mixed["blind_channels"] == []
    assert res_mixed["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_WARMING_UP

    # window_truncated / gap / source_conflict mixed with backfill MUST still fail as broken provenance
    for bad_flag in ("window_truncated", "gap", "source_conflict"):
        trunc_view = deepcopy(mixed_view)
        trunc_view["market_context"]["market_facts"]["volume_price"]["micro_flow"]["fast_4h"][bad_flag] = True
        res_trunc = health_check._check_workbench(
            "http://127.0.0.1:8895",
            token="tok",
            expected_core_version="2.0.7",
            http_get=mk_http(trunc_view),
            now_ms=now_ms,
            require_evidence_channels=True,
        )
        assert res_trunc["ok"] is False
        assert res_trunc["reason"] == "workbench_evidence_contract_broken"
        assert res_trunc["evidence_channels"]["spot_flow"]["capability"] == evidence_qual.CAP_BLIND_UNALIGNED

    # Release candidate manifest does NOT falsely claim USER_DEPLOYED_NATIVE_OBSERVED for 2.0.7 pre-deploy
    rc_manifest = json.loads((ROOT / "deploy" / "signal_audit" / "release_candidate_manifest.json").read_text(encoding="utf-8"))
    fmz_comp = next(c for c in rc_manifest["components"] if c["component"] == "fmz_producer")
    assert fmz_comp["candidate_version"] == "2.0.7"
    assert fmz_comp["runtime_status"] == "LOCAL_CANDIDATE"
    assert fmz_comp["production_status"] == "NOT_NEWLY_DEPLOYED"

    # CLI --require-evidence-channels flag passes through to run_health_check
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    orig_run = health_check.run_health_check
    health_check.run_health_check = fake_run
    try:
        rc = health_check.main(["--skip-network", "--skip-systemd", "--require-evidence-channels"])
    finally:
        health_check.run_health_check = orig_run
    assert rc == 0
    assert captured["expected_core_version"] == "2.0.7"
    assert captured["require_evidence_channels"] is True


# ---------------------------------------------------------------------------
# 6. JS syntax + runtime UI verification via Node
# ---------------------------------------------------------------------------


def test_js_syntax_and_ui_rendering_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available in environment")

    for rel in ("deploy/astra_workbench_v3/locks.js", "deploy/astra_workbench_v3/player-audit.js"):
        chk = subprocess.run([node, "--check", str(ROOT / rel)], capture_output=True, text=True, check=False)
        assert chk.returncode == 0, f"node --check failed for {rel}: {chk.stderr}"

    locks_url = (ROOT / "deploy" / "astra_workbench_v3" / "locks.js").as_uri()
    pa_url = (ROOT / "deploy" / "astra_workbench_v3" / "player-audit.js").as_uri()
    script = f"""
class FakeNode {{
  constructor(tag) {{
    this.tagName = tag;
    this.children = [];
    this.attrs = {{}};
    this.dataset = {{}};
    this.className = "";
    this.textContent = "";
    this.isConnected = true;
  }}
  setAttribute(k, v) {{ this.attrs[k] = String(v); }}
  addEventListener() {{}}
  append(...items) {{
    for (const item of items) {{
      if (item == null || item === false) continue;
      if (typeof item === "string") {{
        const t = new FakeNode("#text");
        t.textContent = item;
        this.children.push(t);
      }} else {{
        this.children.push(item);
      }}
    }}
  }}
  querySelectorAll() {{ return []; }}
}}
globalThis.Node = FakeNode;
globalThis.document = {{
  createElement: (tag) => new FakeNode(tag),
  createElementNS: (_ns, tag) => new FakeNode(tag),
  createTextNode: (txt) => {{ const n = new FakeNode("#text"); n.textContent = String(txt); return n; }},
}};
const textOf = (n) => n.tagName === "#text" ? n.textContent : (n.textContent || "") + n.children.map(textOf).join("");
const findAll = (n, pred, out = []) => {{
  if (pred(n)) out.push(n);
  for (const c of n.children || []) findAll(c, pred, out);
  return out;
}};

const {{ levelsView, changeRow }} = await import({json.dumps(locks_url)});
const {{ renderPlayerAudit }} = await import({json.dumps(pa_url)});

const t0 = 1793070000000;
const t1 = 1793071500000;
const staleRow = {{
  key: "levels.pin",
  label_cn: "本期 Pin",
  status: "stale",
  stale: {{ t0: false, t1: true }},
  t0: {{ value: 80000, unit: "USD", asof_ms: t0 }},
  t1: {{ value: 80000, unit: "USD", asof_ms: t1, stale: true }},
  raw: {{ t1: {{ age_ms: 1500000 }} }},
  to_short_pct: {{ t0: 2.0, t1: 2.0 }},
}};
const sameClockRow = {{
  key: "levels.gamma_put",
  label_cn: "Put Gamma 墙",
  status: "same_clock",
  stale: {{ t0: false, t1: false }},
  t0: {{ value: 77000, unit: "USD", asof_ms: t0 }},
  t1: {{ value: 77000, unit: "USD", asof_ms: t0 }},
  raw: {{ t1: {{ age_ms: 120000 }} }},
  to_short_pct: {{ t0: -1.0, t1: -1.0 }},
}};
const okRow = {{
  key: "levels.gamma_call",
  label_cn: "Call Gamma 墙",
  status: "unchanged",
  stale: {{ t0: false, t1: false }},
  t0: {{ value: 82000, unit: "USD", asof_ms: t0 }},
  t1: {{ value: 82000, unit: "USD", asof_ms: t1 }},
  raw: {{ t1: {{ age_ms: 60000 }} }},
  to_short_pct: {{ t0: 4.0, t1: 4.0 }},
}};

const lv = levelsView([staleRow, sameClockRow, okRow]);
const trs = findAll(lv, (n) => n.tagName === "tr");
const row0 = textOf(trs[1]);
const row1 = textOf(trs[2]);
const row2 = textOf(trs[3]);
if (!row0.includes("数据偏旧·未确认") || row0.includes("未变")) throw new Error("stale row bad: " + row0);
if (!row0.includes("龄 25 分钟")) throw new Error("stale age missing: " + row0);
if (!row1.includes("来源未更新") || row1.includes("未变")) throw new Error("same_clock row bad: " + row1);
if (!row2.includes("未变")) throw new Error("unchanged row bad: " + row2);

const cr = changeRow({{
  key: "quote.visible_credit_btc",
  status: "same_clock",
  t0: {{ value: 0.002, unit: "BTC", asof_ms: t0 }},
  t1: {{ value: 0.002, unit: "BTC", asof_ms: t0 }},
  raw: {{ t1: {{ age_ms: 60000 }} }},
}});
const crTxt = textOf(cr);
if (!crTxt.includes("来源未更新") || crTxt.includes("未变")) throw new Error("changeRow same_clock bad: " + crTxt);

const box = new FakeNode("div");
renderPlayerAudit(box, {{
  cards: [],
  ref: "d1/h1",
  expanded: new Set(),
  detail: {{
    dossier_id: "d1",
    hand_id: "h1",
    state: "Accept",
    side: "put",
    expiry_ms: t1 + 86400000,
    shadow_management: {{
      exit_state: "SHADOW_EXITED",
      exit_terminal: true,
      valuation_tier: "UNPRICED",
      quote_gap_reasons: ["short_ask_missing"],
    }},
    hold_reviews: [{{
      review_id: "rev_1",
      verdict: "HOLD",
      reviewed_at_ms: t1,
      trigger_categories: ["C"],
      pin_eval_mode: "position_only",
      channel_readiness: {{
        channels: {{
          spot_flow: {{ capability: "QUALIFIED_ACTIVE" }},
          pin_structure: {{ capability: "DEGRADED_POSITION_ONLY" }},
          rv24_background: {{ capability: "QUALIFIED_ACTIVE" }},
        }},
        degraded_channels: ["pin_structure"],
        blind_channels: [],
      }},
      public_reply: "hold",
    }}, {{
      review_id: "rev_2",
      verdict: "HOLD",
      reviewed_at_ms: t0,
      pin_eval_mode: "STALE",
      public_reply: "stale hold",
    }}, {{
      review_id: "rev_3",
      verdict: "HOLD",
      reviewed_at_ms: t0 - 1000,
      pin_eval_mode: "same_measure_ard",
      public_reply: "same measure",
    }}],
  }},
}}, {{ authenticated: true }});
const paTxt = textOf(box);
if (!paTxt.includes("影子已退出") || !paTxt.includes("UNPRICED")) throw new Error("SHADOW_EXITED/UNPRICED missing: " + paTxt);
if (!paTxt.includes("Pin 口径：仅持仓参考")) throw new Error("pin_eval_mode badge missing: " + paTxt);
if (!paTxt.includes("Pin 口径：过期(STALE)")) throw new Error("STALE pin_eval_mode badge missing: " + paTxt);
if (!paTxt.includes("Pin 口径：同口径 ARD")) throw new Error("same_measure_ard pin_eval_mode badge missing: " + paTxt);
if (!paTxt.includes("通道降级：pin_structure")) throw new Error("channel_readiness badge missing: " + paTxt);
console.log("JS_OK");
"""
    proc = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node UI check failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "JS_OK" in proc.stdout
