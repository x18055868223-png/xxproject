"""Deterministic P0-REST & Section IV regression suite for Deribit discovery reliability."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import threading
import urllib.error

import pytest

from tools import astra_data_engine_v23 as data_engine
from tools import astra_data_projection_v23 as dp
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_workbench_reading_v1 as reading
from tools import astra_workbench_v20 as workbench
from test_astra_player_tracking_v13 import EXCERPTS, M, RULE, post, serving
from test_astra_workbench_v20 import instruments, make_workbench, native_gate, option_meta, write_fmz


HOUR = 3_600_000
SLOT_16 = int(datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
T = SLOT_16 - 8 * M  # 2026-11-22T15:52:00Z -> expiry SLOT_16 + 16*HOUR is 2026-11-23T08:00:00Z (23NOV26)


def _http_error(status: int, body: bytes, path: str = "/public/get_order_book", headers=None):
    return urllib.error.HTTPError(
        "https://www.deribit.com/api/v2" + path,
        status,
        "Too Many Requests" if status == 429 else "Error",
        headers or {},
        io.BytesIO(body),
    )


def _opt(strike: float, side: str, *, expiry: int, label: str = "23NOV26",
         delta: float | None = None, obs_ms: int | None = None, min_trade: float = 0.1) -> dict:
    suffix = "P" if side == "put" else "C"
    row = {
        "instrument_name": f"BTC-{label}-{int(strike)}-{suffix}",
        "kind": "option",
        "base_currency": "BTC",
        "settlement_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "price_index": "btc_usd",
        "instrument_type": "reversed",
        "option_type": side,
        "state": "open",
        "expiration_timestamp": expiry,
        "contract_size": 1,
        "strike": strike,
        "min_trade_amount": min_trade,
        "settlement_period": "day",
        "taker_commission": 0.0003,
        "is_active": True,
    }
    if delta is not None:
        row["greeks"] = {"delta": delta}
    if obs_ms is not None:
        row["delta_observed_at_ms"] = obs_ms
    return row


def _rich_chain(expiry: int, spot: float = 100_000.0) -> list[dict]:
    """Build multi-strike chain + protection wings on both Put and Call."""
    rows = []
    # Put protection + short strikes: 93k..99k (shorts 95k..99k have -2k wings)
    for k in (93_000, 94_000, 95_000, 96_000, 97_000, 98_000, 99_000):
        rows.append(_opt(k, "put", expiry=expiry))
    # Call short strikes + protection: 101k..107k (shorts 101k..105k have +2k wings)
    for k in (101_000, 102_000, 103_000, 104_000, 105_000, 106_000, 107_000):
        rows.append(_opt(k, "call", expiry=expiry))
    return rows


def _book_payload(name: str, now_ms: int, delta: float, index_price: float = 100_000.0) -> dict:
    return {
        "instrument_name": name,
        "state": "open",
        "timestamp": now_ms - 100,
        "index_price": index_price,
        "mark_price": 0.012 if name.endswith(("-97000-P", "-103000-C")) else 0.004,
        "mark_iv": 52.0,
        "greeks": {"delta": delta, "gamma": 0.0001, "vega": 10.0, "theta": -200.0, "rho": -0.3},
        "bids": [[0.012 if name.endswith(("-97000-P", "-103000-C")) else 0.004, 2.0]],
        "asks": [[0.014 if name.endswith(("-97000-P", "-103000-C")) else 0.005, 2.0]],
    }


def _default_delta(name: str) -> float:
    parts = name.split("-")
    strike = float(parts[2])
    side = parts[3]
    if side == "P":
        table = {99000: -0.45, 98000: -0.40, 97000: -0.35, 96000: -0.26, 95000: -0.18}
        return table.get(int(strike), -0.10)
    table = {101000: 0.45, 102000: 0.40, 103000: 0.35, 104000: 0.26, 105000: 0.18}
    return table.get(int(strike), 0.10)


def _wire_workbench_market(
    tmp_path: Path,
    monkeypatch,
    *,
    clock: list[int],
    expiry: int,
    on_order_book=None,
    index_price_fn=None,
    shadow_dossier_root: Path | None = None,
):
    wb, snaps = make_workbench(tmp_path, clock=lambda: clock[0])
    if shadow_dossier_root is not None:
        wb.config = replace(
            wb.config,
            shadow_dossier_root=shadow_dossier_root,
            shadow_service_token="shadow-secret",
            shadow_experiment=True,
        )
    wb.discovery_capture = wb._default_discovery_capture
    wb.quote_capture = wb._default_quote_capture
    chain = _rich_chain(expiry)
    meta_by_name = {r["instrument_name"]: dict(r) for r in chain}
    wire_log: list[tuple[int, str, dict]] = []

    def fake_disc_get(base, path, params, timeout):
        wire_log.append((clock[0], path, dict(params)))
        if path == "/public/get_index_price":
            spot = index_price_fn() if index_price_fn else 100_000.0
            return {"result": {"index_price": spot}}
        if path == "/public/get_instruments":
            return {"result": [dict(r) for r in chain]}
        if path == "/public/get_order_book":
            name = params["instrument_name"]
            if on_order_book is not None:
                res = on_order_book(name, clock[0])
                if res is not None:
                    return res
            return {"result": _book_payload(name, clock[0], _default_delta(name),
                                             index_price_fn() if index_price_fn else 100_000.0)}
        raise AssertionError(f"unexpected path {path}")

    def fake_quote_get(path, params):
        wire_log.append((clock[0], "/" + path, dict(params)))
        name = params["instrument_name"]
        if path == "public/get_instrument":
            return dict(meta_by_name[name])
        if path == "public/get_order_book":
            return _book_payload(name, clock[0], _default_delta(name),
                                 index_price_fn() if index_price_fn else 100_000.0)
        raise AssertionError(f"unexpected quote path {path}")

    monkeypatch.setattr(discovery, "_public_get", fake_disc_get)
    monkeypatch.setattr(workbench.public_quote, "public_transport", fake_quote_get)
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    return wb, snaps, chain, wire_log


# ---------------------------------------------------------------------------
# Stage 1.5.1: Repro A before/after — 429 partial & auto recovery
# ---------------------------------------------------------------------------

def test_negative_cache_429_preserves_good_side_aborts_storm_and_auto_recovers(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    clock = [T]
    mode = {"call_429": True}
    call_book_attempts = []

    def on_ob(name, ts):
        if name.endswith("-C"):
            call_book_attempts.append((ts, name))
            if mode["call_429"]:
                raise discovery.DeribitPublicError.from_http(
                    _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}',
                                headers={"Retry-After": "10"}),
                    "/public/get_order_book",
                )
        return None

    wb, snaps, _, wire_log = _wire_workbench_market(
        tmp_path, monkeypatch, clock=clock, expiry=expiry, on_order_book=on_ob,
        shadow_dossier_root=tmp_path / "dossiers",
    )
    view0 = wb.refresh(now_ms=clock[0], record_daily=False)
    put0 = next(c for c in view0["candidates"] if c["side"] == "put")
    call0 = next(c for c in view0["candidates"] if c["side"] == "call")

    # Put succeeded while Call aborted immediately after first 429 (did not hammer remaining 4 Call legs!)
    assert put0["status"] == "snapshot"
    assert put0["formal_evidence_eligible"] is True
    assert put0["live_lock_eligible"] is True
    assert call0["status"] == "gap"
    assert call0["formal_evidence_eligible"] is False
    assert call0["live_lock_eligible"] is False
    assert "delta_population_capture_incomplete" in call0["review_gate"]["reasons"]
    assert len(call_book_attempts) == 1
    assert view0["discovery_status"]["capture_state"] == "PARTIAL"

    # Stored record in DataEngine must be PARTIAL (not OK!), keeping 15s/Retry-After recovery
    de_rec = wb.data_engine.read(dp.DISCOVERY, now_ms=clock[0], usage="health", scope=workbench.DISCOVERY_CAPTURE_SCOPE)
    assert de_rec["quality"]["data_state"] == "PARTIAL"
    assert wb.data_engine.read(dp.DISCOVERY, now_ms=clock[0], usage="fetch", scope=workbench.DISCOVERY_CAPTURE_SCOPE)["usage_decision"]["can_use"] is False

    # T+5s: provider recovers, but local 10s rate-limit cooldown prevents hammering (0 discovery wire requests)
    # AND surviving Put side stays available instead of disappearing.
    mode["call_429"] = False
    clock[0] = T + 5_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    disc_wire_before = sum(1 for _ts, p, _par in wire_log if p == "/public/get_order_book")
    view5 = wb.refresh(now_ms=clock[0], record_daily=False)
    disc_wire_at5 = sum(1 for _ts, p, _par in wire_log if p == "/public/get_order_book") - disc_wire_before
    # Only the live 2-leg quote check for surviving Put ran (2 book calls), 0 discovery order book probes!
    assert disc_wire_at5 == 2
    assert next(c for c in view5["candidates"] if c["side"] == "put")["status"] == "snapshot"
    assert next(c for c in view5["candidates"] if c["side"] == "call")["status"] == "gap"

    # T+15s (was broken for 300s before fix!): automatically re-runs without cache clear/restart,
    # reuses fresh cached Put Deltas (0 wire calls for Put!), fetches only missing Call legs, recovers COMPLETE!
    clock[0] = T + 15_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    call_wire_before = list(call_book_attempts)
    wire_before_15 = list(wire_log)
    view15 = wb.refresh(now_ms=clock[0], record_daily=False)
    new_wires = wire_log[len(wire_before_15):]
    new_put_disc_books = [par["instrument_name"] for _ts, p, par in new_wires
                          if p == "/public/get_order_book" and par.get("depth") == 1 and par["instrument_name"].endswith("-P")]
    assert new_put_disc_books == []  # Put Deltas came from verified 120s discovery_delta cache
    assert len(call_book_attempts) - len(call_wire_before) == 5  # All 5 Call shorts fetched
    put15 = next(c for c in view15["candidates"] if c["side"] == "put")
    call15 = next(c for c in view15["candidates"] if c["side"] == "call")
    assert put15["status"] == "snapshot" and put15["formal_evidence_eligible"] is True
    assert call15["status"] == "snapshot" and call15["formal_evidence_eligible"] is True
    assert call15["discovery"]["candidate"]["short_instrument"] == "BTC-23NOV26-103000-C"
    assert view15["discovery_status"]["capture_state"] == "COMPLETE"


def test_rate_limit_on_all_sides_keeps_last_success_clock_and_recovers(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    clock = [T]
    fail = {"all": False}

    def on_ob(name, ts):
        if fail["all"]:
            raise discovery.DeribitPublicError.from_http(
                _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}'),
                "/public/get_order_book",
            )
        return None

    wb, snaps, _, wire_log = _wire_workbench_market(
        tmp_path, monkeypatch, clock=clock, expiry=expiry, on_order_book=on_ob,
        shadow_dossier_root=tmp_path / "dossiers",
    )
    ok_view = wb.refresh(now_ms=clock[0], record_daily=False)
    assert ok_view["discovery_status"]["capture_state"] == "COMPLETE"

    # Advance past 300s discovery TTL and fail first order_book with 429 -> INCOMPLETE + deribit_rate_limited
    clock[0] = T + 310_000
    fail["all"] = True
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    before = len(wire_log)
    err_view = wb.refresh(now_ms=clock[0], record_daily=False)
    after_err = wire_log[before:]
    # Aborted after first 429 on discovery order_book (only 1 order_book wire request, not 10!)
    assert sum(1 for _t, p, _par in after_err if p == "/public/get_order_book") == 1
    assert "upstream_deribit_rate_limited" in err_view["gaps"]
    for c in err_view["candidates"]:
        assert c["status"] == "error"
        assert c["upstream"]["last_success_ms"] == T
        assert c["formal_evidence_eligible"] is False
        assert c["live_lock_eligible"] is False
        assert c["last_valid_candidate"]["valid_at_ms"] == T

    # Inside 10s backoff: 0 wire calls, last_success_ms unchanged
    fail["all"] = False
    clock[0] = T + 315_000
    before5 = len(wire_log)
    err_view2 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert len(wire_log) == before5
    assert err_view2["candidates"][0]["upstream"]["last_success_ms"] == T

    # After backoff clears: auto recovers
    clock[0] = T + 322_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    rec_view = wb.refresh(now_ms=clock[0], record_daily=False)
    assert rec_view["discovery_status"]["capture_state"] == "COMPLETE"
    assert all(c["status"] == "snapshot" and c["live_lock_eligible"] for c in rec_view["candidates"])


# ---------------------------------------------------------------------------
# Stage 1.5.2: Repro B fixed — 4-category Delta classification & completeness
# ---------------------------------------------------------------------------

def test_http_200_missing_or_stale_delta_fails_closed_while_verified_above_max_succeeds():
    expiry = SLOT_16 + 16 * HOUR
    # Case 1: 99000-P is -0.35 (target!), but 98000-P returns HTTP 200 with missing greeks -> MUST fail closed
    chain_missing = [
        _opt(99_000, "put", expiry=expiry, delta=-0.35, obs_ms=T - 1000),
        _opt(97_000, "put", expiry=expiry, delta=-0.20, obs_ms=T - 1000),
        _opt(98_000, "put", expiry=expiry, delta=None, obs_ms=T - 1000),
        _opt(96_000, "put", expiry=expiry, delta=-0.10, obs_ms=T - 1000),
        _opt(101_000, "call", expiry=expiry, delta=0.35, obs_ms=T - 1000),
        _opt(103_000, "call", expiry=expiry, delta=0.20, obs_ms=T - 1000),
    ]
    res = discovery.discover(chain_missing, 100_000.0, T)
    put = res["candidates"]["put"]
    assert put["status"] == "gap"
    assert put["candidate"] is None
    assert "delta_population_greeks_incomplete" in put["gap_reasons"]
    assert "short_delta_missing_or_invalid" in put["gap_reasons"]
    assert put["delta_population"]["verified_eligible"] == 1
    assert put["delta_population"]["delta_missing_or_invalid"] == ["BTC-23NOV26-98000-P"]
    assert put["delta_population"]["complete"] is False
    assert res["candidates"]["call"]["status"] == "candidate"

    # Case 2: 98000-P has stale delta -> also fails closed
    chain_stale = [dict(r) for r in chain_missing]
    chain_stale[2] = _opt(98_000, "put", expiry=expiry, delta=-0.42, obs_ms=T - discovery.DEFAULT_DELTA_MAX_AGE_MS - 5)
    res_stale = discovery.discover(chain_stale, 100_000.0, T)
    assert res_stale["candidates"]["put"]["status"] == "gap"
    assert "short_delta_stale_or_future" in res_stale["candidates"]["put"]["gap_reasons"]

    # Case 3: Contrast — 98000-P has fresh valid delta -0.62 (verified_above_max, NOT unknown!)
    chain_above_max = [dict(r) for r in chain_missing]
    chain_above_max[2] = _opt(98_000, "put", expiry=expiry, delta=-0.62, obs_ms=T - 1000)
    res_ok = discovery.discover(chain_above_max, 100_000.0, T)
    put_ok = res_ok["candidates"]["put"]
    assert put_ok["status"] == "candidate"
    assert put_ok["candidate"]["short_instrument"] == "BTC-23NOV26-99000-P"
    assert put_ok["delta_population"]["complete"] is True
    assert put_ok["delta_population"]["verified_above_max"] == 1
    assert put_ok["delta_population"]["verified_eligible"] == 1


# ---------------------------------------------------------------------------
# Stage 1.5.3 & 1.5.4: Delta cache expiry, interleaving, pacing, maintenance
# ---------------------------------------------------------------------------

def test_verified_delta_cache_expiry_and_no_invalid_caching(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    clock = [T]
    bad_delta = {"drop_call": False}

    def on_ob(name, ts):
        if bad_delta["drop_call"] and name == "BTC-23NOV26-103000-C":
            return {"result": {"instrument_name": name, "state": "open", "timestamp": ts - 100, "greeks": {}}}
        return None

    wb, snaps, _, wire_log = _wire_workbench_market(
        tmp_path, monkeypatch, clock=clock, expiry=expiry, on_order_book=on_ob,
        shadow_dossier_root=tmp_path / "dossiers",
    )
    wb.refresh(now_ms=clock[0], record_daily=False)
    count_round1 = sum(1 for _t, p, par in wire_log if p == "/public/get_order_book" and par.get("depth") == 1)
    assert count_round1 == 10  # 5 put + 5 call shorts

    # At T+125s (> 120s discovery_delta TTL): discovery_delta cache expired -> must re-fetch live!
    clock[0] = T + 125_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    # Wait > 300s on discovery TTL or invalidate by injecting missing delta on fresh capture at T+305s
    clock[0] = T + 305_000
    bad_delta["drop_call"] = True
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    before = len(wire_log)
    v2 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert sum(1 for _t, p, par in wire_log[before:] if p == "/public/get_order_book" and par.get("depth") == 1) == 10
    assert next(c for c in v2["candidates"] if c["side"] == "call")["status"] == "gap"
    # Invalid 103000-C must NOT be stored as usable in greeks_oi cache
    bad_cached = wb.data_engine.read(
        "deribit.btc.option.greeks_oi.v1", now_ms=clock[0], usage="discovery_delta",
        scope={"instrument_name": "BTC-23NOV26-103000-C", "permission": "public_read_only"},
    )
    assert bad_cached["usage_decision"]["can_use"] is False or bad_cached["time"]["observation_end_ms"] < clock[0] - 120_000


def test_fair_interleaving_pacer_and_archive_book_throttle():
    expiry = SLOT_16 + 16 * HOUR
    chain = _rich_chain(expiry)
    requested_order: list[str] = []
    sleeps: list[float] = []
    fake_mono = [100.0]

    def mono():
        return fake_mono[0]

    def sleep(dt):
        sleeps.append(dt)
        fake_mono[0] += dt

    pacer = discovery.DeribitPacer(min_interval_s=0.40, priority_min_interval_s=0.08, max_per_second=4, monotonic=mono, sleep=sleep)

    def get(base, path, params, timeout):
        receipt = {"data_engine_receipt": {"retrieved_at_ms": T}}
        if path == "/public/get_index_price":
            return {"result": {"index_price": 100_000.0}, **receipt}
        if path == "/public/get_instruments":
            return {"result": [dict(r) for r in chain], **receipt}
        requested_order.append(params["instrument_name"])
        return {"result": _book_payload(params["instrument_name"], T, _default_delta(params["instrument_name"])), **receipt}

    res1 = discovery.capture_public_deribit_discovery(captured_ts_ms=T, transport=get, pacer=pacer, start_side="put")
    assert res1["capture_state"] == "COMPLETE"
    # Side-grouped near-OTM first: all Puts 99k..95k, then all Calls 101k..105k
    assert requested_order[:5] == [
        "BTC-23NOV26-99000-P", "BTC-23NOV26-98000-P", "BTC-23NOV26-97000-P",
        "BTC-23NOV26-96000-P", "BTC-23NOV26-95000-P",
    ]
    assert requested_order[5:] == [
        "BTC-23NOV26-101000-C", "BTC-23NOV26-102000-C", "BTC-23NOV26-103000-C",
        "BTC-23NOV26-104000-C", "BTC-23NOV26-105000-C",
    ]
    assert len(sleeps) == 9 and all(abs(dt - 0.40) < 1e-6 for dt in sleeps)
    assert res1["data_identity"]["delta_capture"]["wire_requests_total"] == 10
    assert res1["data_identity"]["wire_requests_total"] == 12

    # Second round starting with call -> all Calls 101k..105k, then all Puts 99k..95k
    requested_order.clear()
    res2 = discovery.capture_public_deribit_discovery(captured_ts_ms=T, transport=get, pacer=pacer, start_side="call")
    assert requested_order[:5] == [
        "BTC-23NOV26-101000-C", "BTC-23NOV26-102000-C", "BTC-23NOV26-103000-C",
        "BTC-23NOV26-104000-C", "BTC-23NOV26-105000-C",
    ]
    assert requested_order[5:] == [
        "BTC-23NOV26-99000-P", "BTC-23NOV26-98000-P", "BTC-23NOV26-97000-P",
        "BTC-23NOV26-96000-P", "BTC-23NOV26-95000-P",
    ]
    assert res2["capture_state"] == "COMPLETE"

    # Research archive Deribit.book spacing
    arch_sleeps: list[float] = []
    arch_mono = [50.0]
    def a_sleep(dt):
        arch_sleeps.append(dt)
        arch_mono[0] += dt
    d_client = archive.Deribit(min_book_interval_s=0.35, monotonic=lambda: arch_mono[0], sleep=a_sleep)
    d_client.min_book_interval_s = 0.35
    d_client.transport = lambda p, par: {"instrument_name": par["instrument_name"], "bids": [[0.01, 1]], "asks": [[0.02, 1]]}
    d_client.book("BTC-23NOV26-97000-P")
    d_client.book("BTC-23NOV26-95000-P")
    assert len(arch_sleeps) == 1 and abs(arch_sleeps[0] - 0.35) < 1e-6


# ---------------------------------------------------------------------------
# Stage 1.5.5: Health scope isolation & historical summary boundary
# ---------------------------------------------------------------------------

def test_health_scope_isolation_and_historical_summary_restart_boundary(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    clock = [T]
    fail = {"mode": False}

    def on_ob(name, ts):
        if fail["mode"]:
            raise discovery.DeribitPublicError.from_http(
                _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}'),
                "/public/get_order_book",
            )
        return None

    wb, snaps, _, _ = _wire_workbench_market(tmp_path, monkeypatch, clock=clock, expiry=expiry, on_order_book=on_ob)
    wb.refresh(now_ms=clock[0], record_daily=False)

    # Inject stale 2.1d projection mirror scope (no capture_contract!) just like live xx-eu scope:0902f819
    old_obs = clock[0] - int(2.1 * 86_400_000)
    wb.data_engine.ingest(
        dp.DISCOVERY, {"captured_ts_ms": old_obs},
        observation_end_ms=old_obs, retrieved_at_ms=None, ingested_at_ms=clock[0],
        scope={"asset": "BTC", "target_hours": 24, "width_usd": 2000, "quantity_btc": 1, "delta_target": 0.35, "delta_max": 0.5},
    )
    page = reading.workbench_page(wb.cached_view(reading_only=True))
    dh_disc = page["data_health"]["products"]["deribit.btc.discovery.current.v1"]
    assert dh_disc["status"] in {"OK", "USAGE_LIMITED"}
    assert dh_disc["current"][0]["observation_end_ms"] == T

    # Advance past TTL, fail discovery, and restart a fresh AstraWorkbench instance on same state_dir
    clock[0] = T + 400_000
    fail["mode"] = True
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])

    wb2 = workbench.AstraWorkbench(wb.config, clock_ms=lambda: clock[0])
    wb2.discovery_capture = wb2._default_discovery_capture
    wb2.quote_capture = wb2._default_quote_capture
    err_view = wb2.refresh(now_ms=clock[0], record_daily=False)
    err_page = reading.workbench_page(wb2.cached_view(reading_only=True))

    for c in err_page["candidates"]:
        assert c["status"] == "error"
        assert c["snapshot"] is None
        assert c["formal_evidence_eligible"] is False
        assert c["live_lock_eligible"] is False
        hist = c["last_valid_candidate"]
        assert hist["valid_at_ms"] == T
        assert hist["is_historical_summary"] is True
        assert hist["formal_evidence_eligible"] is False
        assert hist["live_lock_eligible"] is False

    # Research archive tick, case_file, lock_candidate MUST NOT consume last_valid_candidate!
    arch_dir = tmp_path / "archive"
    arch = archive.Archive(arch_dir)
    rep = archive.tick(arch, now_ms=clock[0], view=err_view, fetch_case=wb2.case_file,
                       deribit=archive.Deribit(transport=lambda p, par: []))
    assert rep["cases"] == 0
    for c in err_view["candidates"]:
        hist_snap_id = c["last_valid_candidate"]["snapshot_id"]
        hist_cand_id = c["last_valid_candidate"]["candidate_id"]
        with pytest.raises(workbench.WorkbenchError):
            wb2.lock_candidate(hist_cand_id, hist_snap_id, f"lock-{hist_snap_id}", authorization="Bearer secret")


# ---------------------------------------------------------------------------
# Stage 1.5.6: P0 Formal Business Chain E2E (15:52 -> 16:17 UTC recovery)
# ---------------------------------------------------------------------------

def test_formal_1552_1617_e2e_recovery_business_chain(tmp_path, monkeypatch):
    # Anchor 16:00 UTC on 2026-11-22 -> expiry 08:00 UTC on 2026-11-23
    slot = T + 8 * M  # 16:00:00Z
    expiry = slot + 16 * HOUR
    clock = [T]  # starts 15:52:00Z
    mode = {"call_429": True}
    llm_calls = [0]

    def on_ob(name, ts):
        if mode["call_429"] and name.endswith("-C"):
            raise discovery.DeribitPublicError.from_http(
                _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}', headers={"Retry-After": "10"}),
                "/public/get_order_book",
            )
        return None

    d_root = tmp_path / "dossiers"
    wb, snaps, _, _ = _wire_workbench_market(
        tmp_path / "wb", monkeypatch, clock=clock, expiry=expiry, on_order_book=on_ob, shadow_dossier_root=d_root,
    )
    arch_root = tmp_path / "archive"
    arch = archive.Archive(arch_root)
    exp_day = dossier.utc(expiry).date().isoformat()
    points = [[slot - 6 * HOUR + i * M, 100_000.0] for i in range(420)]
    fmz_band_ts = slot - 4 * HOUR
    (tmp_path / "band.jsonl").write_text(json.dumps({
        "snapshot_exported_ms": fmz_band_ts - 600_000,
        "source_available_at_ms": fmz_band_ts - 540_000,
        "snapshot_exported_utc8": "2026-11-22 20:00:00",
        "factor_snapshot": {
            "anchor": {"ready": True, "freshness": "FRESH", "band_half": 240.0, "gex_source_ts_ms": fmz_band_ts - 650_000},
            "flow": {"direction": "neutral", "cvd_sum": 12.0},
        },
    }) + "\n", encoding="utf-8")
    archive.archive_band_snapshots(arch, tmp_path / "band.jsonl", now_ms=slot)
    for st in (slot - 4 * HOUR, slot - 10 * M):
        arch.append_line(f"structure/{exp_day}.jsonl", {
            "schema": "structure_snapshot@0.1", "expiry_ms": expiry, "at_ms": st,
            "available_at_ms": st + 30_000, "pin_source": "deribit_gex", "pin_method": "gamma_peak",
            "pin_status": "ok", "expected_move_usd": 1400.0,
            "profile": {"walls": {"pin": 100_000.0}, "top": {"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]}},
        }, at_ms=st + 30_000, kind="structure")

    def fake_arch_transport(path, params):
        if path == "public/get_book_summary_by_currency":
            return []
        if path == "public/get_last_trades_by_currency_and_time":
            return {"trades": [], "has_more": False}
        if path == "public/get_index_chart_data":
            return [[t, p] for t, p in points if t <= clock[0]]
        if path == "public/get_order_book":
            return _book_payload(params["instrument_name"], clock[0], _default_delta(params["instrument_name"]))
        return {}

    d_client = archive.Deribit(transport=fake_arch_transport)

    # 15:52: Put live, Call 429 gap
    v1552 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert v1552["discovery_status"]["capture_state"] == "PARTIAL"

    # 15:59: Workbench periodic refresh while Call still failing preserves Put live
    clock[0] = slot - 60_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    v1559 = wb.refresh(now_ms=clock[0], record_daily=False)
    # 16:00: Put is live on workbench (formal_evidence_eligible=True), Call is still missing (PARTIAL),
    # so archive.tick defers freezing lone Put early in the hour to preserve <60s pair coherence.
    clock[0] = slot + 10_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    v1600 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert v1559["discovery_status"]["capture_state"] in {"PARTIAL", "INCOMPLETE"}
    assert v1600["discovery_status"]["capture_state"] == "PARTIAL"
    assert next(c for c in v1600["candidates"] if c["side"] == "put")["formal_evidence_eligible"] is True
    assert next(c for c in v1600["candidates"] if c["side"] == "call")["formal_evidence_eligible"] is False
    rep1600 = archive.tick(arch, now_ms=clock[0], view=v1600, fetch_case=wb.case_file, deribit=d_client)
    assert rep1600["cases"] == 0

    # Configure Dossiers + Player + Workbench serving
    dossiers = dossier.Dossiers(d_root, lambda: clock[0])
    plan = {
        "plan_id": "daily_r16@2",
        "research": dossier.RESEARCH,
        "mode": "SHADOW_ONLY",
        "candidate_round": "r16",
        "effective_at_ms": slot,
        "expiry_offset_ms": 16 * HOUR,
        "deal_grace_ms": 30 * M,
        "reply_grace_ms": 30 * M,
    }

    def fake_llm(api_key, model, request_body, timeout_s, endpoint=None):
        del api_key, model, request_body, timeout_s, endpoint
        llm_calls[0] += 1
        public_lines = [
            "Put 侧路径核查：" + EXCERPTS["put"],
            EXCERPTS["put_counter"],
            "Call 侧路径核查：" + EXCERPTS["call"],
            EXCERPTS["call_counter"],
            EXCERPTS["comparison"],
            "### Accept / Call",
            "",
            RULE["historical_facts"],
            RULE["accepted_conditions"],
            RULE["confirmation_excerpt"],
        ]
        ann = {**EXCERPTS, "depends_on_ard": False, "condition_plan": {**RULE, "check_interval_ms": 5 * M}}
        return {"choices": [{"message": {"content": "\n".join(public_lines) + "\n<ANNOTATION_JSON>" + json.dumps(ann, ensure_ascii=False) + "</ANNOTATION_JSON>"}, "finish_reason": "stop"}]}

    def fake_dossier_public(method, **kwargs):
        if method == "get_instruments":
            return [dict(r) for r in _rich_chain(expiry)]
        raise AssertionError(method)

    monkeypatch.setattr(dossier, "public", fake_dossier_public)
    # Also ensure dossier.archive.Deribit returns covered index_path during collect
    orig_deribit_cls = archive.Deribit
    monkeypatch.setattr(dossier.archive, "Deribit", lambda: orig_deribit_cls(transport=fake_arch_transport))

    with serving(wb) as base:
        service = post(base, wb)
        # 16:02: Call native case still missing -> NO_EVIDENCE, 0 LLM calls!
        clock[0] = slot + 2 * M
        r1602 = tracking.auto_deal(dossiers, plan, arch_root, service, llm_config={"transport": fake_llm})
        assert llm_calls[0] == 0 and r1602[0]["status"] == "NO_EVIDENCE"

        # 16:05: Provider recovers -> wb.refresh auto-recovers Call -> BOTH Put and Call COMPLETE!
        mode["call_429"] = False
        clock[0] = slot + 5 * M
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
        v1605 = wb.refresh(now_ms=clock[0], record_daily=False)
        assert v1605["discovery_status"]["capture_state"] == "COMPLETE"

        # 16:10: Archive tick creates BOTH synchronized Put & Call cases (<= 60s apart) + r16 level cards
        clock[0] = slot + 10 * M
        wb._expiry_gamma = {
            "schema": "astra_expiry_gamma_v1@1.0.0",
            "status": "ok",
            "computed_at_ms": clock[0] - 5_000,
            "expiries": {
                "23NOV26": {
                    "expiry": "23NOV26",
                    "expiry_ms": expiry,
                    "forward_usd": 100_000.0,
                    "expected_move_usd": 1400.0,
                    "walls": {"pin": 100_000.0, "gamma_call": 103_000.0, "gamma_put": 97_000.0,
                              "oi_call": 103_000.0, "oi_put": 97_000.0},
                    "top": {"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]},
                }
            },
        }
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
        v1610 = wb.refresh(now_ms=clock[0], record_daily=False)
        rep1610 = archive.tick(arch, now_ms=clock[0], view=v1610, fetch_case=wb.case_file, deribit=d_client)
        assert rep1610["cases"] == 2
        built_times = []
        lvl_file = arch_root / "levels" / f"{exp_day}.jsonl"
        existing_r16 = set()
        if lvl_file.exists():
            for ln in lvl_file.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    row = json.loads(ln)
                    if row.get("round") == "r16":
                        existing_r16.add(row.get("side"))
        for p_case in sorted((arch_root / "cases" / dossier.utc(slot).date().isoformat()).glob("16_*.json.gz")):
            c_obj = json.loads(gzip.decompress(p_case.read_bytes()).decode("utf-8"))
            built_times.append(c_obj["build"]["built_at_ms"])
            c_side = dossier.player.values(c_obj, "L1.contract")["side"]
            if c_side not in existing_r16:
                card = dossier.levels.build_card(
                    expiry_ms=expiry, rnd="r16", side=c_side, read_at_ms=c_obj["build"]["built_at_ms"],
                    walls={"pin_source": "deribit_gex", "pin_method": "gamma_peak", "pin_status": "ok",
                           "walls": {"pin": 100_000.0}, "top": {"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]}},
                    points=[pt for pt in points if pt[0] <= c_obj["build"]["built_at_ms"]],
                    case=c_obj, contract=dossier.player.values(c_obj, "L1.contract"), snapshots=[],
                )
                arch.append_line(f"levels/{exp_day}.jsonl", card, at_ms=clock[0], kind="level")
        assert len(built_times) == 2 and abs(built_times[0] - built_times[1]) <= 60_000

        # 16:12: auto_deal freezes evidence -> invokes mock LLM ONCE -> Accept Call -> refresh_quote + lock -> SHADOW_LOCK_CREATED!
        clock[0] = slot + 12 * M
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
        wb.refresh(now_ms=clock[0], record_daily=False)
        r1612 = tracking.auto_deal(dossiers, plan, arch_root, service, llm_config={"transport": fake_llm})
        assert llm_calls[0] == 1
        assert r1612[0]["decision"] == "Accept"
        assert r1612[0]["link"]["state"] == "SHADOW_LOCK_CREATED"
        locked_eps = wb.locked_candidates.list()
        assert len(locked_eps) == 1
        assert wb.locked_candidates.detail(locked_eps[0]["episode_id"])["episode"]["contract"]["short_instrument"] == "BTC-23NOV26-103000-C"
        assert r1612[0]["link"]["execution_allowed"] is False
        assert r1612[0]["link"]["t0"]["tracker_episode_id"] == locked_eps[0]["episode_id"]

        # 16:17: second auto_deal is 100% idempotent (1 hand, 1 LLM call, 1 lock)
        clock[0] = slot + 17 * M
        r1617 = tracking.auto_deal(dossiers, plan, arch_root, service, llm_config={"transport": fake_llm})
        assert llm_calls[0] == 1
        assert len(list((d_root / "dossiers").glob("*/hands/hand_*"))) == 1

    # Also prove persistent PARTIAL past minute 25 (e.g. hour 17:30) still archives surviving Put side for research
    arch2 = archive.Archive(tmp_path / "arch_late_partial")
    clock_late = slot + 90 * M  # 17:30:00Z
    late_rep = archive.tick(arch2, now_ms=clock_late, view=v1600, fetch_case=wb.case_file, deribit=d_client)
    assert late_rep["cases"] == 1


def test_past_grace_recovery_never_manufactures_expired_hand(tmp_path, monkeypatch):
    slot = T + 8 * M
    expiry = slot + 16 * HOUR
    clock = [slot + 35 * M]  # past 30m deal_grace_ms
    d_root = tmp_path / "dossiers"
    wb, _, _, _ = _wire_workbench_market(
        tmp_path / "wb", monkeypatch, clock=clock, expiry=expiry, shadow_dossier_root=d_root,
    )
    dossiers = dossier.Dossiers(d_root, lambda: clock[0])
    plan = {"plan_id": "daily_r16@2", "research": dossier.RESEARCH, "mode": "SHADOW_ONLY",
            "candidate_round": "r16", "effective_at_ms": slot, "expiry_offset_ms": 16 * HOUR,
            "deal_grace_ms": 30 * M, "reply_grace_ms": 30 * M}
    calls = [0]
    with serving(wb) as base:
        out = tracking.auto_deal(dossiers, plan, tmp_path / "arch", post(base, wb),
                                 llm_config={"transport": lambda *a, **kw: calls.append(1)})
    assert calls == [0]
    assert out == [] or all(x.get("status") != "VALID" for x in out)


# ---------------------------------------------------------------------------
# Stage 1.5.7: Section IV Nov 20 Concurrent Rotation & Shared-View Regression
# ---------------------------------------------------------------------------

def test_nov20_candidate_rotation_never_mislocks_new_contract_and_reproduces_shared_view_race(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    slot = T + 8 * M
    clock = [slot + 2 * M]
    spot_box = {"spot": 100_000.0}

    def on_ob(name, ts):
        # Initially 103k-C is +0.35; when spot moves to 101k, make 104k-C +0.35 and 103k-C +0.48!
        if spot_box["spot"] > 100_500.0 and name.endswith("-C"):
            d_map = {102000: 0.55, 103000: 0.48, 104000: 0.35, 105000: 0.25, 106000: 0.16}
            strike = int(name.split("-")[2])
            return {"result": _book_payload(name, ts, d_map.get(strike, 0.15), spot_box["spot"])}
        return None

    d_root = tmp_path / "dossiers"
    wb, snaps, chain, _ = _wire_workbench_market(
        tmp_path, monkeypatch, clock=clock, expiry=expiry,
        on_order_book=on_ob, index_price_fn=lambda: spot_box["spot"], shadow_dossier_root=d_root,
    )
    v0 = wb.refresh(now_ms=clock[0], record_daily=False)
    call0 = next(c for c in v0["candidates"] if c["side"] == "call")
    assert call0["snapshot"]["contract"]["short_instrument"] == "BTC-23NOV26-103000-C"

    # Build a hand frozen on 103000-C, then AI Accepts 103000-C
    d = dossier.Dossiers(d_root, lambda: clock[0])
    accepted_snap = call0["snapshot"]
    cases, cards, native = [], [], []
    points = [[t, 100_000.0] for t in range(clock[0] - dossier.DAY, clock[0] + 1, M)]
    for c in v0["candidates"]:
        s = c["snapshot"]
        case = wb.case_file(s["snapshot_id"])
        read_ms = case["build"]["built_at_ms"]
        walls = dict(expiry_ms=expiry, walls={"pin": 100_000.0},
                     top={"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]})
        dossier.player.entry(case, "L3.expiry_walls").update(values=walls, asof_ms=read_ms, quality="ok")
        contract = dossier.player.values(case, "L1.contract")
        cards.append(dossier.levels.build_card(expiry_ms=expiry, rnd="r16", side=contract["side"],
                     read_at_ms=read_ms, walls=walls, points=points, case=case, contract=contract, snapshots=[]))
        cases.append(case)
        native.extend(dict(instrument_name=contract[f"{leg}_instrument"], expiration_timestamp=expiry,
                           settlement_currency="BTC", is_active=True) for leg in ("short", "long"))
    path = dict(schema="index_path@0.1", index_name="btc_usd", expiry_ms=expiry, points=points, fetched_at_ms=clock[0])
    hand = d.expect(expiry, "SCHEDULED", plan={
        "plan_id": "daily_r16@2", "research": dossier.RESEARCH, "mode": "SHADOW_ONLY",
        "candidate_round": "r16", "effective_at_ms": slot, "expiry_offset_ms": 16 * HOUR,
        "deal_grace_ms": 30 * M, "reply_grace_ms": 30 * M,
    }, slot=slot)
    d.deal(hand, dossier.seal_source(cases, cards, path, [], native, received_at_ms=clock[0], source="TEST_FROZEN"))
    requested_ms = clock[0]
    clock[0] += 100
    raw = ("**Accept / Call**\n" + "\n".join(EXCERPTS.values()) + "\n" +
           "\n".join(RULE[k] for k in ("historical_facts", "accepted_conditions", "confirmation_excerpt"))).encode()
    d.respond(hand, "fixture-reply", "RECEIVED", raw, EXCERPTS,
              model="fixture-model-1.3", requested=requested_ms, decided=clock[0],
              configuration={"reasoning_effort": "fixture"})

    # Candidate discovery rotates after Accept from 103000-C -> 104000-C
    clock[0] += 305_000
    spot_box["spot"] = 101_000.0
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    v_rot = wb.refresh(now_ms=clock[0], record_daily=False)
    call_rot = next(c for c in v_rot["candidates"] if c["side"] == "call")
    assert call_rot["snapshot"]["contract"]["short_instrument"] == "BTC-23NOV26-104000-C"

    with serving(wb) as base:
        service = post(base, wb)
        # 1. Trying to lock directly without refreshing exact accepted quote CANNOT mislock 104000-C as 103000-C
        res_without_refresh = tracking.link(d, hand, service, RULE)
        assert res_without_refresh["state"] == "LINK_UNVERIFIED"
        assert res_without_refresh["reason"] == "exact_current_contract_not_found"
        assert len(wb.locked_candidates.list()) == 0

        # 2. Rotation STILL blocks refresh_quote and lock cleanly without ever mislocking 104000-C
        ref_rot = service("refresh_quote", {"dossier_id": hand.parent.parent.name, "hand_id": hand.name})
        assert ref_rot["state"] == "QUOTE_UNAVAILABLE" and ref_rot["error"] == "exact_current_contract_not_found"
        assert tracking.link(d, hand, service, RULE) == {"state": "LINK_UNVERIFIED", "reason": "exact_current_contract_not_found"}
        assert len(wb.locked_candidates.list()) == 0

        # 3. Direct lock_candidate with mismatched snapshot_id vs candidate_id rejects lock_candidate_mismatch
        with pytest.raises(workbench.WorkbenchError) as exc_info:
            wb.lock_candidate(accepted_snap["candidate_id"], call_rot["snapshot"]["snapshot_id"],
                              "mismatch-key", authorization="Bearer secret")
        assert exc_info.value.code == "lock_candidate_mismatch"

        # 4. Also verify _link_with_fresh_quote records QUOTE_UNAVAILABLE detail cleanly
        refresh_results = []
        def wrap_service(action, payload):
            if action == "refresh_quote":
                refresh_results.append("QUOTE_UNAVAILABLE")
                return {"state": "QUOTE_UNAVAILABLE", "error": "public_quote_stale"}
            return service(action, payload)
        monkeypatch.setattr(tracking.time, "sleep", lambda _s: None)
        masked = tracking._link_with_fresh_quote(d, hand, wrap_service, RULE)
        assert len(refresh_results) >= 1
        assert masked == {"state": "LINK_UNVERIFIED", "reason": "exact_current_contract_not_found"}

        # 5. Concurrent shared-view overwrite fix: once 103000-C is current again, refresh_quote succeeds
        # and even if a concurrent background refresh transiently blanks _current_view (no conflicting rotation),
        # the fresh slot lets exact 103000-C lock cleanly while still honoring <=5s confirm_quote & <=2s skew.
        clock[0] += 305_000
        spot_box["spot"] = 100_000.0
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
        v_back = wb.refresh(now_ms=clock[0], record_daily=False)
        assert next(c for c in v_back["candidates"] if c["side"] == "call")["snapshot"]["contract"]["short_instrument"] == "BTC-23NOV26-103000-C"
        ref_ok = service("refresh_quote", {"dossier_id": hand.parent.parent.name, "hand_id": hand.name})
        assert ref_ok["state"] == "QUOTE_REFRESHED"
        with wb._view_lock:
            wb._current_view["candidates"] = []  # simulate transient gap clearing _current_view before lock()
        recovered_race = tracking.link(d, hand, service, RULE)
        assert recovered_race["state"] == "SHADOW_LOCK_CREATED"
        assert recovered_race["lock_mode"] == "DELAYED_LOCK" and recovered_race["delayed_lock"] is True
        assert len(wb.locked_candidates.list()) == 1
        assert wb.locked_candidates.detail(wb.locked_candidates.list()[0]["episode_id"])["episode"]["contract"]["short_instrument"] == "BTC-23NOV26-103000-C"


# ---------------------------------------------------------------------------
# Stage 2: REST Pacing Tuning, Shared Token Bucket & Archive Dedup
# ---------------------------------------------------------------------------

def _21_leg_chain(expiry: int) -> list[dict]:
    """Build 21 metadata-qualified short candidates (16 Put + 5 Call) + protection wings, matching live xx-eu."""
    rows = []
    # Put strikes: 82k..99k -> shorts 84k..99k (16 legs) all have -2k wings (82k..83k are protection)
    for k in range(82_000, 100_000, 1_000):
        rows.append(_opt(k, "put", expiry=expiry))
    # Call strikes: 101k..107k -> shorts 101k..105k (5 legs) all have +2k wings
    for k in range(101_000, 108_000, 1_000):
        rows.append(_opt(k, "call", expiry=expiry))
    return rows


def test_pacer_and_archive_defaults_and_pair_token_bucket():
    import inspect

    p = discovery.DeribitPacer()
    assert p.min_interval_s == 1.05
    assert p.priority_min_interval_s == 0.08
    assert p.max_per_second == 2
    assert p.burst_capacity == 2.0

    d = archive.Deribit()
    assert d.min_book_interval_s == 1.05
    assert d.max_rate_limit_waits == 1
    assert d.cooldown_default_s == 10.0

    assert inspect.signature(discovery._with_public_greeks).parameters["max_round_s"].default == 45.0
    assert inspect.signature(discovery.capture_public_deribit_discovery).parameters["max_round_s"].default == 45.0

    mono_now = [100.0]
    grants: list[tuple[str, float]] = []

    def sleep(dt: float) -> None:
        mono_now[0] += dt

    pacer = discovery.DeribitPacer(monotonic=lambda: mono_now[0], sleep=sleep)
    for idx in range(5):
        ok, why = pacer.acquire(priority="discovery", cost=1.0, deadline_mono=mono_now[0] + 10.0)
        assert ok and why is None
        grants.append((f"disc_{idx}", mono_now[0]))
        mono_now[0] += 0.020  # 20ms EU RTT

    # Immediately acquire Put pair (cost=2.0 on leg1, cost=0.0 on leg2) + Call pair right after discovery #5
    for side in ("put", "call"):
        ok1, why1 = pacer.acquire(priority="quote", cost=2.0, deadline_mono=mono_now[0] + 8.0)
        assert ok1 and why1 is None
        grants.append((f"{side}_short", mono_now[0]))
        mono_now[0] += 0.020
        ok2, why2 = pacer.acquire(priority="quote", cost=0.0, deadline_mono=mono_now[0] + 5.0)
        assert ok2 and why2 is None
        grants.append((f"{side}_long", mono_now[0]))
        mono_now[0] += 0.020

    ts = [t for _, t in grants]
    # Discovery wait after leg 5 before Put short avoids colliding on call #6/#7
    assert ts[5] - ts[4] >= 1.00 - 1e-6
    # Put & Call 2-leg pairs stay tight internally (<= 150 ms) so skew << 2000 ms
    assert 0.08 - 1e-6 <= ts[6] - ts[5] <= 0.15
    assert 0.08 - 1e-6 <= ts[8] - ts[7] <= 0.15
    # Call short waits ~2 tokens after Put short
    assert ts[7] - ts[5] >= 2.00 - 1e-6
    # No sliding 1s window ever has more than 2 grants
    for i, t in enumerate(ts):
        assert sum(1 for x in ts if t - 0.999 <= x <= t + 1e-9) <= 2, f"burst >2 around grant {i}"


def test_21_leg_cold_sweep_plus_immediate_quotes_under_eu_burst_ceiling(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    chain = _21_leg_chain(expiry)
    meta_by_name = {r["instrument_name"]: dict(r) for r in chain}

    def run_scenario(*, use_old_pacer: bool):
        mono_now = [1000.0]
        clock = [T]
        book_wire_times: list[float] = []
        rate_limit_errors: list[tuple[float, str]] = []
        quote_depths: list[tuple[str, int]] = []

        def advance(dt_s: float) -> None:
            mono_now[0] += dt_s
            clock[0] += int(round(dt_s * 1000))

        wb, snaps = make_workbench(tmp_path / ("old" if use_old_pacer else "new"), clock=lambda: clock[0])
        wb.discovery_capture = wb._default_discovery_capture
        wb.quote_capture = wb._default_quote_capture
        if use_old_pacer:
            wb._deribit_pacer = discovery.DeribitPacer(
                min_interval_s=0.40, priority_min_interval_s=0.08, max_per_second=4,
                monotonic=lambda: mono_now[0], sleep=advance,
            )
        else:
            wb._deribit_pacer._monotonic = lambda: mono_now[0]
            wb._deribit_pacer._sleep = advance
        wb._force_discovery_pacing = True
        wb._force_quote_pacing = True

        def check_eu_ceiling(name: str) -> None:
            # Strict empirical EU IP ceiling: >= 8 calls inside 3.5 s triggers HTTP 429 (7 calls pass)
            recent = [t for t in book_wire_times if mono_now[0] - t <= 3.5]
            if len(recent) >= 7:
                rate_limit_errors.append((mono_now[0], name))
                raise discovery.DeribitPublicError.from_http(
                    _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}', headers={"Retry-After": "10"}),
                    "/public/get_order_book",
                )
            book_wire_times.append(mono_now[0])
            advance(0.020)  # 20ms EU RTT

        def fake_disc_get(base, path, params, timeout):
            if path == "/public/get_index_price":
                advance(0.020)
                return {"result": {"index_price": 100_000.0}}
            if path == "/public/get_instruments":
                advance(0.020)
                return {"result": [dict(r) for r in chain]}
            if path == "/public/get_order_book":
                name = params["instrument_name"]
                check_eu_ceiling(name)
                return {"result": _book_payload(name, clock[0], _default_delta(name))}
            raise AssertionError(path)

        def fake_quote_get(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(meta_by_name[name])
            if path == "public/get_order_book":
                quote_depths.append((name, int(params.get("depth") or 1)))
                try:
                    check_eu_ceiling(name)
                except discovery.DeribitPublicError as exc:
                    raise _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}', headers={"Retry-After": "10"}) from exc
                return _book_payload(name, clock[0], _default_delta(name))
            raise AssertionError(path)

        monkeypatch.setattr(discovery, "_public_get", fake_disc_get)
        monkeypatch.setattr(workbench.public_quote, "public_transport", fake_quote_get)
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])

        t0 = mono_now[0]
        view = wb.refresh(now_ms=clock[0], record_daily=False)
        elapsed_s = mono_now[0] - t0
        return view, elapsed_s, book_wire_times, rate_limit_errors, quote_depths

    # Old 400ms pacing deterministically hits HTTP 429 on leg #8
    old_view, _, old_wires, old_429s, _ = run_scenario(use_old_pacer=True)
    assert len(old_429s) == 1
    assert len(old_wires) == 7
    assert old_view["discovery_status"]["capture_state"] in {"PARTIAL", "INCOMPLETE"}
    assert old_view["discovery_status"]["upstream_code"] == "deribit_rate_limited"

    # New 1.05s shared token-bucket + pair-reserved quote finishes all 21 legs + 4 depth=5 quotes with 0 HTTP 429
    new_view, new_elapsed_s, new_wires, new_429s, new_q_depths = run_scenario(use_old_pacer=False)
    assert new_429s == []
    assert len(new_wires) == 25  # 21 discovery (depth=1) + 4 quote (depth=5)
    assert new_elapsed_s < 30.0
    assert new_view["discovery_status"]["capture_state"] == "COMPLETE"
    assert len(new_q_depths) == 4 and all(depth == 5 for _, depth in new_q_depths)
    for side in ("put", "call"):
        cand = next(c for c in new_view["candidates"] if c["side"] == side)
        assert cand["status"] == "snapshot"
        assert cand["formal_evidence_eligible"] is True
        snap = cand["snapshot"]
        q = snap["market"]["quote"]
        ex_ts = q["exchange_times_ms"]
        assert q["gap_reasons"] == []
        assert abs(ex_ts["short"] - ex_ts["long"]) <= 500
        assert max(q["valuation_ts_ms"] - ex_ts["short"], q["valuation_ts_ms"] - ex_ts["long"]) <= 5_000


def test_quote_delta_reuse_and_strict_freshness_invariants(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    clock = [T]
    d_root = tmp_path / "dossiers"
    wb, snaps, _, wire_log = _wire_workbench_market(
        tmp_path, monkeypatch, clock=clock, expiry=expiry, shadow_dossier_root=d_root,
    )

    v1 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert v1["discovery_status"]["capture_state"] == "COMPLETE"
    assert v1["candidates"][0]["discovery"]["candidate"]["short_instrument"] == "BTC-23NOV26-97000-P"
    assert v1["candidates"][1]["discovery"]["candidate"]["short_instrument"] == "BTC-23NOV26-103000-C"
    # _record_quote_books populated _delta_mem_cache with verified depth=5 quote legs
    for quoted_leg in ("BTC-23NOV26-97000-P", "BTC-23NOV26-95000-P", "BTC-23NOV26-103000-C", "BTC-23NOV26-105000-C"):
        assert quoted_leg in wb._delta_mem_cache

    # At T+270s, discovery is a 300s DataEngine cache hit while initial T=0 deltas (>120s) have expired;
    # routine refresh captures fresh depth=5 live quotes and updates _delta_mem_cache for those 4 legs.
    clock[0] += 270_000
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    v_mid = wb.refresh(now_ms=clock[0], record_daily=False)
    assert v_mid["discovery_status"]["capture_state"] == "COMPLETE"

    # At T+305s (>300s discovery TTL), next cold sweep runs: T=0 deltas are expired, T+270s quote deltas (35s old) hit!
    clock[0] += 35_000
    wire_log.clear()
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    v2 = wb.refresh(now_ms=clock[0], record_daily=False)
    assert v2["discovery_status"]["capture_state"] == "COMPLETE"
    disc_ob_legs = [p["instrument_name"] for _, path, p in wire_log if path == "/public/get_order_book" and p.get("depth") == 1]
    quote_ob_legs = [p["instrument_name"] for _, path, p in wire_log if path == "/public/get_order_book" and p.get("depth") == 5]
    # Quoted legs (40s old) were reused by discovery instead of re-fetched on the wire
    for quoted_leg in ("BTC-23NOV26-97000-P", "BTC-23NOV26-95000-P", "BTC-23NOV26-103000-C", "BTC-23NOV26-105000-C"):
        assert quoted_leg not in disc_ob_legs
    assert len(disc_ob_legs) == 6  # 10 total short legs - 4 reused from live quote cache
    # Live 2-leg quotes STILL fetched fresh depth=5 books on the wire and kept identical candidate ranking
    assert quote_ob_legs == ["BTC-23NOV26-97000-P", "BTC-23NOV26-95000-P", "BTC-23NOV26-103000-C", "BTC-23NOV26-105000-C"]
    assert v2["candidates"][0]["snapshot"]["candidate_id"] == v1["candidates"][0]["snapshot"]["candidate_id"]
    assert v2["candidates"][1]["snapshot"]["candidate_id"] == v1["candidates"][1]["snapshot"]["candidate_id"]
    assert v2["candidates"][0]["snapshot"]["contract"] == v1["candidates"][0]["snapshot"]["contract"]
    assert v2["candidates"][1]["snapshot"]["contract"] == v1["candidates"][1]["snapshot"]["contract"]


def test_concurrent_archive_and_workbench_ledger_complete_across_cycles_and_bounded_recovery(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    arch_root = tmp_path / "archive"
    arch = archive.Archive(arch_root)
    mono_now = [500.0]
    clock = [T + 10 * M]

    def advance(dt_s: float) -> None:
        mono_now[0] += dt_s
        clock[0] += int(round(dt_s * 1000))

    # 8 tracked contracts sharing overlapping strikes -> 16 leg slots -> only 10 unique option legs
    overlapping = [
        ("put", 97_000.0, 95_000.0),
        ("put", 98_000.0, 96_000.0),
        ("put", 99_000.0, 97_000.0),
        ("put", 96_000.0, 94_000.0),
        ("call", 103_000.0, 105_000.0),
        ("call", 102_000.0, 104_000.0),
        ("call", 101_000.0, 103_000.0),
        ("call", 104_000.0, 106_000.0),
    ]
    st = arch.load_state()
    for side, ks, kl in overlapping:
        suf = "P" if side == "put" else "C"
        sname, lname = f"BTC-23NOV26-{int(ks)}-{suf}", f"BTC-23NOV26-{int(kl)}-{suf}"
        ckey = f"{sname}|{lname}"
        st["contracts"][ckey] = {
            "key": ckey, "side": side, "short_instrument": sname, "long_instrument": lname,
            "short_strike_usd": ks, "long_strike_usd": kl, "expiry_ms": expiry,
            "first_seen_ms": T, "cases": [], "last_row": None,
        }
    arch.save_state(st)

    wb, _, _, _ = _wire_workbench_market(tmp_path / "wb", monkeypatch, clock=clock, expiry=expiry)
    wb._deribit_pacer._monotonic = lambda: mono_now[0]
    wb._deribit_pacer._sleep = advance
    wb._force_discovery_pacing = True
    wb._force_quote_pacing = True

    shared_wire_times: list[float] = []
    archive_book_calls: list[str] = []

    def shared_rate_check() -> None:
        recent = [t for t in shared_wire_times if mono_now[0] - t <= 3.5]
        if len(recent) >= 7:
            raise ValueError("DERIBIT_HTTP_429 on public/get_order_book")
        shared_wire_times.append(mono_now[0])
        advance(0.020)

    def arch_transport(path, params):
        if path == "public/get_index_chart_data":
            return [[T - 3_600_000, 100_000.0], [clock[0], 100_000.0]]
        if path == "public/get_book_summary_by_currency":
            return []
        if path == "public/get_last_trades_by_currency_and_time":
            return {"trades": [], "has_more": False}
        if path == "public/get_order_book":
            shared_rate_check()
            name = params["instrument_name"]
            archive_book_calls.append(name)
            return _book_payload(name, clock[0], _default_delta(name))
        raise AssertionError(path)

    # Run 3 consecutive archive ticks alongside live workbench refreshes (crossing near-expiry base triggers)
    for cycle in range(3):
        clock[0] = T + (cycle * 35 + 10) * M
        mono_now[0] += 600.0
        archive_book_calls.clear()
        v = wb.refresh(now_ms=clock[0], record_daily=False)
        advance(2.0)  # minor offset between workbench refresh and cron tick
        d_client = archive.Deribit(monotonic=lambda: mono_now[0], sleep=advance)
        d_client.min_book_interval_s = 1.05
        d_client.transport = arch_transport
        rep = archive.tick(arch, now_ms=clock[0], view=v, fetch_case=wb.case_file, deribit=d_client)
        assert rep["errors"] == []
        # 8 contracts use 16 leg slots -> 6 unique Put strikes + 6 unique Call strikes = 12 unique wire calls
        assert len(archive_book_calls) == 12 and len(set(archive_book_calls)) == 12
        assert rep["rows"] == 8

    # Verify bounded single-stray-429 recovery (waits 10s once, recovers remaining contracts)
    stray_calls = [0]
    sleeps_rec: list[float] = []
    d_stray = archive.Deribit(monotonic=lambda: mono_now[0], sleep=lambda s: (sleeps_rec.append(s), advance(s)))
    d_stray.min_book_interval_s = 1.05
    def stray_transport(path, params):
        if path == "public/get_order_book":
            stray_calls[0] += 1
            if stray_calls[0] == 3:
                raise ValueError("DERIBIT_HTTP_429 on public/get_order_book")
            return _book_payload(params["instrument_name"], clock[0], _default_delta(params["instrument_name"]))
        return arch_transport(path, params)
    d_stray.transport = stray_transport
    clock[0] += 35 * M
    mono_now[0] += 600.0
    rep_stray = archive.tick(arch, now_ms=clock[0], view=None, fetch_case=wb.case_file, deribit=d_stray)
    assert rep_stray["errors"] == []
    assert rep_stray["rows"] == 8
    assert any(abs(s - 10.0) < 1e-6 for s in sleeps_rec)

    # Verify persistent 429 fails fast after 1 bounded wait (never infinite retries)
    sleeps_persist: list[float] = []
    d_persist = archive.Deribit(monotonic=lambda: mono_now[0], sleep=lambda s: (sleeps_persist.append(s), advance(s)))
    d_persist.min_book_interval_s = 1.05
    def persist_transport(path, params):
        if path == "public/get_order_book":
            raise ValueError("DERIBIT_HTTP_429 on public/get_order_book")
        return arch_transport(path, params)
    d_persist.transport = persist_transport
    clock[0] += 35 * M
    mono_now[0] += 600.0
    rep_persist = archive.tick(arch, now_ms=clock[0], view=None, fetch_case=wb.case_file, deribit=d_persist)
    ledger_errs = [e for e in rep_persist["errors"] if e.get("step") == "ledger"]
    assert len(ledger_errs) == 8
    assert sum(1 for s in sleeps_persist if abs(s - 10.0) < 1e-6) == 1

    # Verify archive yields at entry when workbench discovery_status reports active cooldown
    sleeps_yield: list[float] = []
    d_yield = archive.Deribit(monotonic=lambda: mono_now[0], sleep=lambda s: (sleeps_yield.append(s), advance(s)))
    d_yield.min_book_interval_s = 1.05
    d_yield.transport = arch_transport
    clock[0] += 35 * M
    mono_now[0] += 600.0
    view_cooling = {"discovery_status": {"capture_state": "PARTIAL", "next_retry_at_ms": clock[0] + 4_000}, "candidates": []}
    rep_yield = archive.tick(arch, now_ms=clock[0], view=view_cooling, fetch_case=wb.case_file, deribit=d_yield)
    assert rep_yield["errors"] == []
    assert sleeps_yield and abs(sleeps_yield[0] - 4.5) < 1e-6


# ---------------------------------------------------------------------------
# Stage 3: Pending Lock Auto-Retry Inside Decision Validity Window
# ---------------------------------------------------------------------------

def _build_frozen_accept_hand(
    tmp_path: Path,
    monkeypatch,
    *,
    expiry: int,
    clock: list[int],
    call_429: dict[str, bool],
    spot_box: dict[str, float] | None = None,
    plan_extra: dict | None = None,
    quote_bid_103k: dict[str, float] | None = None,
):
    slot = expiry - 16 * HOUR
    d_root = tmp_path / "dossiers"
    spot = spot_box or {"spot": 100_000.0}

    def on_ob(name, ts):
        if call_429.get("on") and name.endswith("-C"):
            raise discovery.DeribitPublicError.from_http(
                _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}', headers={"Retry-After": "10"}),
                "/public/get_order_book",
            )
        if spot["spot"] > 100_500.0 and name.endswith("-C"):
            d_map = {102000: 0.55, 103000: 0.48, 104000: 0.35, 105000: 0.25, 106000: 0.16}
            strike = int(name.split("-")[2])
            return {"result": _book_payload(name, ts, d_map.get(strike, 0.15), spot["spot"])}
        return None

    wb, snaps, _, wire_log = _wire_workbench_market(
        tmp_path / "wb", monkeypatch, clock=clock, expiry=expiry,
        on_order_book=on_ob, index_price_fn=lambda: spot["spot"], shadow_dossier_root=d_root,
    )
    orig_quote_tr = workbench.public_quote.public_transport

    def custom_quote_tr(path, params):
        res = orig_quote_tr(path, params)
        if path == "public/get_order_book" and quote_bid_103k and params["instrument_name"].endswith("-103000-C"):
            res = dict(res)
            res["bids"] = [[quote_bid_103k["bid"], 2.0]]
            res["asks"] = [[round(quote_bid_103k["bid"] + 0.002, 6), 2.0]]
        return res

    monkeypatch.setattr(workbench.public_quote, "public_transport", custom_quote_tr)
    v0 = wb.refresh(now_ms=clock[0], record_daily=False)
    d = dossier.Dossiers(d_root, lambda: clock[0])
    cases, cards, native = [], [], []
    points = [[t, 100_000.0] for t in range(clock[0] - dossier.DAY, clock[0] + 1, M)]
    for c in v0["candidates"]:
        s = c["snapshot"]
        case = wb.case_file(s["snapshot_id"])
        read_ms = case["build"]["built_at_ms"]
        walls = dict(expiry_ms=expiry, walls={"pin": 100_000.0},
                     top={"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]})
        dossier.player.entry(case, "L3.expiry_walls").update(values=walls, asof_ms=read_ms, quality="ok")
        contract = dossier.player.values(case, "L1.contract")
        cards.append(dossier.levels.build_card(expiry_ms=expiry, rnd="r16", side=contract["side"],
                     read_at_ms=read_ms, walls=walls, points=points, case=case, contract=contract, snapshots=[]))
        cases.append(case)
        native.extend(dict(instrument_name=contract[f"{leg}_instrument"], expiration_timestamp=expiry,
                           settlement_currency="BTC", is_active=True) for leg in ("short", "long"))
    path = dict(schema="index_path@0.1", index_name="btc_usd", expiry_ms=expiry, points=points, fetched_at_ms=clock[0])
    plan = {
        "plan_id": "daily_r16@2", "research": dossier.RESEARCH, "mode": "SHADOW_ONLY",
        "candidate_round": "r16", "effective_at_ms": slot, "expiry_offset_ms": 16 * HOUR,
        "deal_grace_ms": 30 * M, "reply_grace_ms": 30 * M, **(plan_extra or {}),
    }
    hand = d.expect(expiry, "SCHEDULED", plan=plan, slot=slot)
    d.deal(hand, dossier.seal_source(cases, cards, path, [], native, received_at_ms=clock[0], source="TEST_FROZEN"))
    requested_ms = clock[0]
    clock[0] += 1_000
    raw = ("**Accept / Call**\n" + "\n".join(EXCERPTS.values()) + "\n" +
           "\n".join(RULE[k] for k in ("historical_facts", "accepted_conditions", "confirmation_excerpt"))).encode()
    d.respond(hand, "auto_accept01", "RECEIVED", raw, EXCERPTS,
              model="deepseek-v4-pro", requested=requested_ms, decided=clock[0],
              configuration={"reasoning_effort": "high"})
    return wb, snaps, d, hand, points, wire_log


def test_pending_lock_429_recovery_delayed_lock_and_idempotent_polls(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    slot = expiry - 16 * HOUR
    clock = [slot + 2 * M]
    mode = {"on": False}
    q_bid = {"bid": 0.012}
    wb, snaps, d, hand, _, _ = _build_frozen_accept_hand(
        tmp_path, monkeypatch, expiry=expiry, clock=clock, call_429=mode, quote_bid_103k=q_bid,
    )
    decided_ms = dossier.read(hand / "decision.json")["decided_at_ms"]
    frozen_input_bytes = (hand / "evidence" / "input.md").read_bytes()
    frozen_decision_bytes = (hand / "decision.json").read_bytes()

    # Now Deribit Call side hits 429; initial link fails and enters PENDING_LOCK with 60-minute deadline
    clock[0] += 305_000
    mode["on"] = True
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)

    with serving(wb) as base:
        svc = post(base, wb)
        first = tracking._link_with_fresh_quote(d, hand, svc, RULE)
        assert first["state"] == "LINK_UNVERIFIED"
        assert len(wb.locked_candidates.list()) == 0

    pending = tracking.load(hand / "tracking" / "pending_lock.json")
    assert pending["state"] == "PENDING_LOCK"
    assert pending["deadline_ms"] == min(expiry, decided_ms + 60 * M)
    assert pending["decided_at_ms"] == decided_ms
    assert pending["contract"]["short_instrument"] == "BTC-23NOV26-103000-C"
    assert len(pending["attempts"]) >= 1 and all(
        a["detail"] == "candidate_temporarily_unavailable" for a in pending["attempts"]
    )
    assert tracking.audit_record(hand, clock[0], {})["tracking_state"] == "PENDING_LOCK"

    # Several natural refreshes while 429 persists: records retries, never locks wrong side or backfills
    for step_m in (5, 10, 15):
        clock[0] += step_m * M
        write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
        wb.refresh(now_ms=clock[0], record_daily=False)
        assert not (hand / "tracking" / "link.json").exists()
        assert wb.locked_candidates.list() == []
    pending_mid = tracking.load(hand / "tracking" / "pending_lock.json")
    assert pending_mid["state"] == "PENDING_LOCK" and len(pending_mid["attempts"]) >= 4

    # Provider recovers at ~decided+36m with updated live bid (0.018): natural wb.refresh() locks exact 103000-C!
    clock[0] += 6 * M
    mode["on"] = False
    q_bid["bid"] = 0.018
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)

    link_rec = tracking.load(hand / "tracking" / "link.json")
    assert link_rec["state"] == "SHADOW_LOCK_CREATED"
    assert link_rec["lock_mode"] == "DELAYED_LOCK" and link_rec["delayed_lock"] is True
    assert len(link_rec["retry_history"]) >= 4
    assert link_rec["origin"]["decided_at_ms"] == decided_ms
    assert link_rec["t0"]["locked_at_ms"] == clock[0]
    assert link_rec["t0"]["offset_from_decision_ms"] == clock[0] - decided_ms
    # Uses genuine recovered live quote (0.018 - 0.005 = 0.013), never historical entry quote (0.012 - 0.005 = 0.007)
    assert abs(link_rec["t0"]["quote"]["credit_btc"] - 0.013) < 1e-9
    assert (hand / "evidence" / "input.md").read_bytes() == frozen_input_bytes
    assert (hand / "decision.json").read_bytes() == frozen_decision_bytes
    assert tracking.load(hand / "tracking" / "pending_lock.json")["state"] == "LOCKED"
    assert len(wb.locked_candidates.list()) == 1

    # Repeated polling / refresh / link calls after success remain 100% idempotent
    clock[0] += M
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    with serving(wb) as base:
        assert tracking._link_with_fresh_quote(d, hand, post(base, wb), RULE) == link_rec
    assert tracking.load(hand / "tracking" / "link.json") == link_rec
    assert len(wb.locked_candidates.list()) == 1


def test_pending_lock_restart_persistence_and_15m_legacy_compat(tmp_path, monkeypatch):
    expiry = SLOT_16 + 16 * HOUR
    slot = expiry - 16 * HOUR
    clock = [slot + 2 * M]
    mode = {"on": False}
    wb, snaps, d, hand, _, _ = _build_frozen_accept_hand(
        tmp_path, monkeypatch, expiry=expiry, clock=clock, call_429=mode,
    )
    clock[0] += 305_000
    mode["on"] = True
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    with serving(wb) as base:
        assert tracking._link_with_fresh_quote(d, hand, post(base, wb), RULE)["state"] == "LINK_UNVERIFIED"
    del wb

    # Simulated workbench service restart while PENDING_LOCK is on disk
    clock[0] += 10 * M
    mode["on"] = False
    restarted, snaps2, _, _ = _wire_workbench_market(
        tmp_path / "wb", monkeypatch, clock=clock, expiry=expiry, shadow_dossier_root=tmp_path / "dossiers",
    )
    write_fmz(snaps2, native_gate(ts=clock[0] - 1000), now=clock[0])
    restarted.refresh(now_ms=clock[0], record_daily=False)
    link_rec = tracking.load(hand / "tracking" / "link.json")
    assert link_rec["lock_mode"] == "DELAYED_LOCK" and link_rec["delayed_lock"] is True
    assert len(restarted.locked_candidates.list()) == 1

    # Also prove pre-existing 15-minute legacy request.json files remain provable via request_proof()
    req_path = hand / "tracking" / "request.json"
    req_obj = tracking.load(req_path)
    legacy_15m_req = {**req_obj, "deadline_ms": req_obj["accepted_at_ms"] + 15 * M}
    dossier.write(req_path, dossier.encode(legacy_15m_req), replace=True)
    proof_15m, _ = tracking.request_proof(hand)
    assert proof_15m["deadline_ms"] == legacy_15m_req["deadline_ms"]


def test_pending_lock_rotation_persistent_missing_timeout_and_declared_validity(tmp_path, monkeypatch):
    # Case A: Contract changed + timeout at 60m -> never mislocks 104000-C, terminates cleanly
    expiry = SLOT_16 + 16 * HOUR
    slot = expiry - 16 * HOUR
    clock = [slot + 2 * M]
    mode = {"on": False}
    spot = {"spot": 100_000.0}
    wb, snaps, d, hand, _, _ = _build_frozen_accept_hand(
        tmp_path / "case_a", monkeypatch, expiry=expiry, clock=clock, call_429=mode, spot_box=spot,
    )
    clock[0] += 305_000
    spot["spot"] = 101_000.0  # discovery rotates Call to 104000-C while decision accepted 103000-C
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    with serving(wb) as base:
        assert tracking._link_with_fresh_quote(d, hand, post(base, wb), RULE)["reason"] == "exact_current_contract_not_found"
    assert len(wb.locked_candidates.list()) == 0

    clock[0] += 61 * M  # cross 60-minute validity deadline
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    assert not (hand / "tracking" / "link.json").exists()
    assert tracking.load(hand / "tracking" / "final_block.json")["reason"] == "shadow_handoff_expired"
    term_pending = tracking.load(hand / "tracking" / "pending_lock.json")
    assert term_pending["state"] == "TERMINATED" and term_pending["terminal_reason"] == "shadow_handoff_expired"
    attempts_at_term = len(term_pending["attempts"])
    # Further refreshes after termination do not retry or append attempts
    clock[0] += 5 * M
    write_fmz(snaps, native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    assert len(tracking.load(hand / "tracking" / "pending_lock.json")["attempts"]) == attempts_at_term
    assert wb.locked_candidates.list() == []

    # Case B: Declared shorter decision validity (20 minutes < 60 minutes) must strictly bind deadline_ms
    clock_b = [slot + 2 * M]
    mode_b = {"on": False}
    wb_b, snaps_b, d_b, hand_b, _, _ = _build_frozen_accept_hand(
        tmp_path / "case_b", monkeypatch, expiry=expiry, clock=clock_b, call_429=mode_b,
        plan_extra={"decision_valid_ms": 20 * M},
    )
    decided_b = dossier.read(hand_b / "decision.json")["decided_at_ms"]
    assert tracking.accepted(hand_b)["deadline_ms"] == decided_b + 20 * M
    clock_b[0] += 305_000
    mode_b["on"] = True
    write_fmz(snaps_b, native_gate(ts=clock_b[0] - 1000), now=clock_b[0])
    wb_b.refresh(now_ms=clock_b[0], record_daily=False)
    with serving(wb_b) as base:
        assert tracking._link_with_fresh_quote(d_b, hand_b, post(base, wb_b), RULE)["state"] == "LINK_UNVERIFIED"
    # Even though < 60m elapsed (21m), 20m declared decision validity expired -> terminates cleanly
    clock_b[0] = decided_b + 21 * M
    mode_b["on"] = False
    write_fmz(snaps_b, native_gate(ts=clock_b[0] - 1000), now=clock_b[0])
    wb_b.refresh(now_ms=clock_b[0], record_daily=False)
    assert tracking.load(hand_b / "tracking" / "final_block.json")["reason"] == "shadow_handoff_expired"
    assert wb_b.locked_candidates.list() == []


