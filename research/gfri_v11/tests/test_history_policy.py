"""Unit tests for GFRI v1.1 historical references, shadow policy, audit, and console."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import time
import tracemalloc

import pytest

from research.gfri_v11.config import (
    CODE_VERSION,
    DEFAULT_CONFIG,
    SCHEMA_AUDIT,
    SCHEMA_EVAL,
    GfriConfig,
    ShadowFlags,
)
from research.gfri_v11.history import (
    INDEX_SCHEMA,
    MS_PER_DAY,
    ShadowLedger,
    compute_long_term_ref,
    compute_near_term_ref,
    extract_index_entry,
    select_snapshot_asof_episode,
)
from research.gfri_v11.policy import (
    build_audit_record,
    evaluate_shadow_policy,
    reconstruct_policy_state_from_ledger,
    render_minimal_console,
)

BASE_TS_MS = 1_760_000_000_000  # 2025-10-09 UTC


def _make_side(
    side: str,
    gfri: float | None,
    *,
    status: str = "VALID",
    flow_usd: float | None = None,
    depth_usd: float | None = 1_000_000.0,
    single_blip: bool = False,
    is_amplifying: bool | None = None,
) -> dict:
    if is_amplifying is None and gfri is not None:
        is_amplifying = gfri > 0.0
    if flow_usd is None and gfri is not None and depth_usd is not None:
        flow_usd = -(gfri * depth_usd) if gfri > 0.0 else 500_000.0
    return {
        "side": side,
        "shock": "down_1pct" if side == "PUT" else "up_1pct",
        "status": status,
        "valid_count": 3 if status != "UNKNOWN" else 0,
        "sample_count": 3,
        "gfri_confirmed": gfri,
        "gfri_fast_max": gfri,
        "gfri_fast_min": gfri,
        "gfri_latest_raw": gfri,
        "spread_ratio": 1.05 if gfri is not None else None,
        "single_blip_detected": single_blip,
        "raw_hedge_flow_usd": flow_usd,
        "liquidity_depth_usd": depth_usd,
        "signed_ratio": (-gfri if is_amplifying else 0.5) if gfri is not None else None,
        "is_amplifying": is_amplifying,
        "sample_values": [gfri, gfri, gfri] if gfri is not None else [],
        "sample_depths_usd": [depth_usd, depth_usd, depth_usd] if depth_usd is not None else [],
    }


def _make_cycle(
    ts_ms: int,
    *,
    put_gfri: float | None = 1.0,
    call_gfri: float | None = 0.5,
    data_quality: str = "VALID",
    confirmation_status: str = "CONFIRMED",
    put_status: str = "VALID",
    call_status: str = "VALID",
    put_depth: float | None = 1_000_000.0,
    call_depth: float | None = 1_000_000.0,
    put_flow: float | None = None,
    call_flow: float | None = None,
    put_single_blip: bool = False,
    call_single_blip: bool = False,
    methodology_version: str = "1.1",
    universe_policy: str = "all_deribit_btc",
    primary_method: str = "HEDGE_FLOW_NATIVE",
    depth_venue: str = "DERIBIT_BTC_PERP",
    code_version: str = CODE_VERSION,
) -> dict:
    return {
        "schema": SCHEMA_EVAL,
        "freeze_at_ms": ts_ms,
        "data_quality": data_quality,
        "confirmation_status": confirmation_status,
        "reasons": [] if data_quality == "VALID" else ["TEST_REASON"],
        "sample_count": 3,
        "shares_same_gex_snapshot": True,
        "independent_gamma_estimates": 1,
        "primary_method": primary_method,
        "comparability": {
            "methodology_version": methodology_version,
            "universe_policy": universe_policy,
            "primary_method": primary_method,
            "depth_venue": depth_venue,
            "code_version": code_version,
        },
        "timestamps": {
            "gex_data_ts_ms": ts_ms - 60_000,
            "gex_computed_at_ms": ts_ms - 55_000,
            "gex_received_at_ms": ts_ms - 50_000,
            "ob_exchange_ts_ms": ts_ms - 5_000,
            "ob_received_at_ms": ts_ms - 4_000,
            "freeze_at_ms": ts_ms,
            "ob_skew_ms": 50_000,
            "gex_age_ms": 60_000,
            "oi_source_lag_hours": 8,
            "oi_lag_note": "deribit_public_oi_reported_with_8h_delay_per_docs",
        },
        "provenance": {
            "gex_snapshot_id": f"snap-{ts_ms}",
            "gex_universe_hash": "uhash123",
            "total_open_interest": 120_000.0,
            "gex_spot_usd": 65_000.0,
            "ob_ref_price_usd": 65_010.0,
            "gex_sha256": "gexsha256",
            "ob_sha256": "obsha256",
            "bid_levels": 500,
            "ask_levels": 500,
        },
        "cross_venue_ref": {"okx_depth_usd": 900_000.0},
        "research_submetrics": {"net_gex_linear": 0.9},
        "put": _make_side(
            "PUT",
            put_gfri,
            status=put_status,
            flow_usd=put_flow,
            depth_usd=put_depth,
            single_blip=put_single_blip,
        ),
        "call": _make_side(
            "CALL",
            call_gfri,
            status=call_status,
            flow_usd=call_flow,
            depth_usd=call_depth,
            single_blip=call_single_blip,
        ),
        "sample_refs": [{"sample_idx": 0, "ob_sha256": "ob0"}],
    }


def _make_ready_near_history(now_ms: int, *, put_gfri: float = 1.0, call_gfri: float = 0.5) -> list[dict]:
    return [
        _make_cycle(now_ms - (i + 1) * 30 * 60_000, put_gfri=put_gfri, call_gfri=call_gfri)
        for i in range(4)
    ]


def _make_ready_long_history(
    now_ms: int,
    *,
    days: int = 35,
    samples_per_day: int = 2,
    put_gfri: float = 1.0,
    call_gfri: float = 0.5,
) -> list[dict]:
    records: list[dict] = []
    for d in range(1, days + 1):
        for s in range(samples_per_day):
            ts = now_ms - d * MS_PER_DAY - s * 3600_000
            records.append(_make_cycle(ts, put_gfri=put_gfri, call_gfri=call_gfri))
    return records


# ---------------------------------------------------------------------------
# Category D: Historical Reference Tests
# ---------------------------------------------------------------------------


def test_near_term_warmup_and_window_cutoff() -> None:
    now = BASE_TS_MS
    # Only 3 samples in window (<4), plus 1 stale (5h ago) and 1 future (+1min)
    history = [
        _make_cycle(now - 30 * 60_000, put_gfri=1.0),
        _make_cycle(now - 60 * 60_000, put_gfri=1.1),
        _make_cycle(now - 90 * 60_000, put_gfri=1.2),
        _make_cycle(now - 5 * 3600_000, put_gfri=9.9),  # outside 4h window
        _make_cycle(now + 60_000, put_gfri=9.9),       # strictly future -> excluded
    ]
    current = _make_cycle(now, put_gfri=2.8, call_gfri=0.4)
    near = compute_near_term_ref(history, current)

    assert near["put"]["state"] == "WARMING_UP"
    assert near["put"]["insufficient_history"] is True
    assert near["put"]["sample_count"] == 3
    assert near["call"]["state"] == "WARMING_UP"


def test_near_term_ready_per_side_and_driver_decomposition() -> None:
    now = BASE_TS_MS
    # Put has 4 valid samples at 1.0x (depth 1.0M, flow -1.0M); call has 2 UNKNOWN -> call stays WARMING_UP
    history = [
        _make_cycle(now - 1 * 3600_000, put_gfri=1.0, put_depth=1_000_000.0, put_flow=-1_000_000.0, call_gfri=None, call_status="UNKNOWN"),
        _make_cycle(now - 2 * 3600_000, put_gfri=1.0, put_depth=1_000_000.0, put_flow=-1_000_000.0, call_gfri=None, call_status="UNKNOWN"),
        _make_cycle(now - 3 * 3600_000, put_gfri=1.0, put_depth=1_000_000.0, put_flow=-1_000_000.0, call_gfri=0.5),
        _make_cycle(now - 3 * 3600_000 - 1800_000, put_gfri=1.0, put_depth=1_000_000.0, put_flow=-1_000_000.0, call_gfri=0.5),
    ]

    # Case 1: Liquidity drop (depth drops 50% -> 500k, flow unchanged -> GFRI rises to 2.0x)
    curr_liq = _make_cycle(now, put_gfri=2.0, put_depth=500_000.0, put_flow=-1_000_000.0, call_gfri=0.6)
    near_liq = compute_near_term_ref(history, curr_liq)
    assert near_liq["put"]["state"] == "READY"
    assert near_liq["put"]["driver"] == "LIQUIDITY_DROP"
    assert near_liq["put"]["coincided_with_liquidity_drop"] is True
    assert near_liq["put"]["depth_change_pct"] == pytest.approx(-0.5)
    assert near_liq["put"]["delta_ratio"] == pytest.approx(2.0)
    assert near_liq["call"]["state"] == "WARMING_UP"

    # Case 2: Hedge flow rise (depth unchanged 1.0M, flow doubles to -2.0M)
    curr_flow = _make_cycle(now, put_gfri=2.0, put_depth=1_000_000.0, put_flow=-2_000_000.0)
    near_flow = compute_near_term_ref(history, curr_flow)
    assert near_flow["put"]["driver"] == "HEDGE_FLOW_RISE"
    assert near_flow["put"]["coincided_with_liquidity_drop"] is False
    assert near_flow["put"]["abs_flow_change_pct"] == pytest.approx(1.0)

    # Case 3: Both depth drop and flow rise
    curr_both = _make_cycle(now, put_gfri=3.0, put_depth=500_000.0, put_flow=-1_500_000.0)
    near_both = compute_near_term_ref(history, curr_both)
    assert near_both["put"]["driver"] == "BOTH"
    assert near_both["put"]["coincided_with_liquidity_drop"] is True


def test_long_term_60d_dropoff_and_comparability_filter() -> None:
    now = BASE_TS_MS
    history = [
        # Valid within 60d
        _make_cycle(now - 10 * MS_PER_DAY, put_gfri=1.0),
        _make_cycle(now - 20 * MS_PER_DAY, put_gfri=1.0),
        # Outside 60d window -> excluded
        _make_cycle(now - 61 * MS_PER_DAY, put_gfri=9.0),
        # Comparability mismatches -> all excluded
        _make_cycle(now - 2 * MS_PER_DAY, put_gfri=9.0, methodology_version="1.0"),
        _make_cycle(now - 3 * MS_PER_DAY, put_gfri=9.0, universe_policy="filtered"),
        _make_cycle(now - 4 * MS_PER_DAY, put_gfri=9.0, primary_method="NET_GEX_LINEAR"),
        _make_cycle(now - 5 * MS_PER_DAY, put_gfri=9.0, depth_venue="OKX_BTC_SWAP"),
        _make_cycle(now - 6 * MS_PER_DAY, put_gfri=9.0, code_version="1.0.0"),
    ]
    current = _make_cycle(now, put_gfri=1.5)
    long_ref = compute_long_term_ref(history, current)

    assert long_ref["put"]["sample_count"] == 2
    assert long_ref["put"]["distinct_days"] == 2
    assert long_ref["put"]["state"] == "ACCUMULATING"


def test_long_term_intraday_weighting_cap_prevents_flooding() -> None:
    now = BASE_TS_MS
    # 34 days with 2 samples/day at 1.0x + 1 single day (day 1) flooded with 200 samples at 9.0x
    history: list[dict] = []
    for d in range(2, 36):
        for s in range(2):
            history.append(_make_cycle(now - d * MS_PER_DAY - s * 1800_000, put_gfri=1.0))
    for k in range(200):
        history.append(_make_cycle(now - 1 * MS_PER_DAY - k * 60_000, put_gfri=9.0))

    current = _make_cycle(now, put_gfri=1.5)
    long_ref = compute_long_term_ref(history, current)

    assert long_ref["put"]["distinct_days"] == 35
    assert long_ref["put"]["state"] == "READY"
    # Day 1's 200 samples share a single day's decay weight (~1 day out of 35), so 1.5x is still > P85!
    assert long_ref["put"]["weighted_median"] == pytest.approx(1.0)
    assert long_ref["put"]["percentile"] is not None
    assert long_ref["put"]["percentile"] > 80.0


def test_long_term_cold_start_bands_and_never_fake_percentile() -> None:
    now = BASE_TS_MS
    current = _make_cycle(now, put_gfri=2.4, call_gfri=0.4)

    # < 7 days -> ACCUMULATING
    hist_3d = [_make_cycle(now - d * MS_PER_DAY, put_gfri=1.0) for d in range(1, 4)]
    ref_3d = compute_long_term_ref(hist_3d, current)
    assert ref_3d["put"]["state"] == "ACCUMULATING"
    assert ref_3d["put"]["percentile"] is None
    assert ref_3d["put"]["coarse_band"] is None
    assert ref_3d["put"]["display_label"] == "积累中 (3/30天)"

    # 7-29 days -> COARSE_BAND_ONLY (never fake P99)
    hist_15d = [_make_cycle(now - d * MS_PER_DAY, put_gfri=1.0) for d in range(1, 16)]
    ref_15d = compute_long_term_ref(hist_15d, current)
    assert ref_15d["put"]["state"] == "COARSE_BAND_ONLY"
    assert ref_15d["put"]["percentile"] is None
    assert ref_15d["put"]["coarse_band"] == "ELEVATED"
    assert ref_15d["put"]["display_label"] == "粗粒度:ELEVATED (15/30天)"
    assert ref_15d["call"]["coarse_band"] == "LOW"

    # >= 30 days and n_eff >= 30 -> READY
    hist_35d = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0)
    ref_35d = compute_long_term_ref(hist_35d, current)
    assert ref_35d["put"]["state"] == "READY"
    assert ref_35d["put"]["percentile"] is not None
    assert ref_35d["put"]["display_label"].startswith("P")


def test_regime_drift_warning_and_anti_masking() -> None:
    now = BASE_TS_MS
    # Persistently stressed regime: 35 days around 3.2x -> weighted median >= 2.50x
    history = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=3.2)
    near_hist = _make_ready_near_history(now, put_gfri=2.7)
    current = _make_cycle(now, put_gfri=2.6, call_gfri=0.4)

    near_ref = compute_near_term_ref(near_hist, current)
    long_ref = compute_long_term_ref(history, current)

    assert long_ref["put"]["state"] == "READY"
    assert long_ref["put"]["percentile"] is not None
    assert long_ref["put"]["percentile"] < 10.0  # low relative percentile
    assert long_ref["put"]["regime_drift_warning"] is True
    assert "mask" in (long_ref["put"]["regime_drift_note"] or "").lower()

    # Adaptive anti-masking: low percentile MUST NOT suppress SHADOW_PAUSE
    policy = evaluate_shadow_policy(current, near_ref, long_ref)
    assert policy["put_decision"] == "SHADOW_PAUSE"
    assert policy["shadow_decision"] == "SHADOW_PAUSE_PUT"
    assert "REGIME_DRIFT_ANTI_MASKING_ACTIVE" in policy["candidate_reason_codes"]["put"]


# ---------------------------------------------------------------------------
# Category E: Shadow Policy, Audit & Console Tests
# ---------------------------------------------------------------------------


def test_positive_gamma_dampening_no_veto_and_caveat() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=0.0, call_gfri=0.0)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=0.0, call_gfri=0.0)
    current = _make_cycle(now, put_gfri=0.0, call_gfri=0.0)

    near_ref = compute_near_term_ref(near_hist, current)
    long_ref = compute_long_term_ref(long_hist, current)
    policy = evaluate_shadow_policy(current, near_ref, long_ref)

    assert policy["shadow_decision"] == "NO_VETO"
    assert policy["put_decision"] == "NO_VETO"
    assert policy["call_decision"] == "NO_VETO"
    assert policy["no_veto_does_not_imply_safe"] is True
    assert "no_veto_does_not_imply_safe" in policy["caveats"]
    assert policy["param_calibration_status"] == "UNVALIDATED_CANDIDATE_PARAMS"


def test_unknown_data_never_emits_no_veto_and_preserves_partial_pause() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=2.8, call_gfri=0.4)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.4)

    # Cycle with PUT triggering SHADOW_PAUSE and CALL UNKNOWN
    current = _make_cycle(now, put_gfri=2.8, call_gfri=None, call_status="UNKNOWN")
    near_ref = compute_near_term_ref(near_hist, current)
    long_ref = compute_long_term_ref(long_hist, current)

    policy = evaluate_shadow_policy(current, near_ref, long_ref)
    assert policy["shadow_decision"] == "UNKNOWN"
    assert policy["put_decision"] == "SHADOW_PAUSE"
    assert policy["call_decision"] == "UNKNOWN"
    assert policy["partial_pause_side"] == "PUT"

    # Full data_quality=UNKNOWN also yields UNKNOWN and preserves prior active_pause without releasing
    prior_paused = {
        "put": {"active_pause": True, "pause_consecutive": 2, "release_consecutive": 1, "last_decision": "SHADOW_PAUSE"},
        "call": {"active_pause": False, "pause_consecutive": 0, "release_consecutive": 0, "last_decision": "NO_VETO"},
    }
    bad_cycle = _make_cycle(now, put_gfri=None, call_gfri=None, data_quality="UNKNOWN", put_status="UNKNOWN", call_status="UNKNOWN")
    policy_bad = evaluate_shadow_policy(bad_cycle, near_ref, long_ref, prior_state=prior_paused)
    assert policy_bad["shadow_decision"] == "UNKNOWN"
    assert policy_bad["next_policy_state"]["put"]["active_pause"] is True
    assert policy_bad["next_policy_state"]["put"]["release_consecutive"] == 0


def test_single_blip_does_not_trigger_shadow_pause() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=2.7, call_gfri=0.5)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.5)

    current = _make_cycle(
        now,
        put_gfri=3.5,
        call_gfri=0.5,
        confirmation_status="SINGLE_BLIP_FILTERED",
        put_single_blip=True,
    )
    near_ref = compute_near_term_ref(near_hist, current)
    long_ref = compute_long_term_ref(long_hist, current)
    policy = evaluate_shadow_policy(current, near_ref, long_ref)

    assert policy["put_decision"] == "NO_VETO"
    assert policy["shadow_decision"] == "NO_VETO"
    assert "SINGLE_BLIP_FILTERED_NO_TRIGGER" in policy["candidate_reason_codes"]["put"]


def test_sustained_2cycle_triggers_put_call_and_both() -> None:
    now = BASE_TS_MS
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=1.0)

    # PUT only sustained 2 cycles
    near_hist_put = _make_ready_near_history(now, put_gfri=1.0, call_gfri=0.5)
    near_hist_put[0] = _make_cycle(now - 30 * 60_000, put_gfri=2.6, call_gfri=0.5)
    curr_put = _make_cycle(now, put_gfri=2.7, call_gfri=0.5)
    pol_put = evaluate_shadow_policy(
        curr_put,
        compute_near_term_ref(near_hist_put, curr_put),
        compute_long_term_ref(long_hist, curr_put),
    )
    assert pol_put["shadow_decision"] == "SHADOW_PAUSE_PUT"

    # CALL only sustained 2 cycles
    near_hist_call = _make_ready_near_history(now, put_gfri=0.5, call_gfri=1.0)
    near_hist_call[0] = _make_cycle(now - 30 * 60_000, put_gfri=0.5, call_gfri=2.6)
    curr_call = _make_cycle(now, put_gfri=0.5, call_gfri=2.8)
    pol_call = evaluate_shadow_policy(
        curr_call,
        compute_near_term_ref(near_hist_call, curr_call),
        compute_long_term_ref(long_hist, curr_call),
    )
    assert pol_call["shadow_decision"] == "SHADOW_PAUSE_CALL"

    # BOTH sustained 2 cycles
    near_hist_both = _make_ready_near_history(now, put_gfri=1.0, call_gfri=1.0)
    near_hist_both[0] = _make_cycle(now - 30 * 60_000, put_gfri=2.6, call_gfri=2.6)
    curr_both = _make_cycle(now, put_gfri=2.9, call_gfri=2.7)
    pol_both = evaluate_shadow_policy(
        curr_both,
        compute_near_term_ref(near_hist_both, curr_both),
        compute_long_term_ref(long_hist, curr_both),
    )
    assert pol_both["shadow_decision"] == "SHADOW_PAUSE_BOTH"


def test_hysteresis_keeps_at_2_2x_and_releases_after_2_cycles_below_2_0x() -> None:
    now = BASE_TS_MS
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.5)
    near_hist = _make_ready_near_history(now, put_gfri=1.0, call_gfri=0.5)
    near_hist[0] = _make_cycle(now - 30 * 60_000, put_gfri=2.6, call_gfri=0.5)

    # Cycle 0: enters SHADOW_PAUSE_PUT at 2.6x
    c0 = _make_cycle(now, put_gfri=2.6, call_gfri=0.5)
    p0 = evaluate_shadow_policy(
        c0,
        compute_near_term_ref(near_hist, c0),
        compute_long_term_ref(long_hist, c0),
    )
    assert p0["shadow_decision"] == "SHADOW_PAUSE_PUT"
    state = p0["next_policy_state"]

    # Cycle 1: 2.2x (< 2.5x, >= 2.0x) -> stays SHADOW_PAUSE_PUT
    c1 = _make_cycle(now + 10 * 60_000, put_gfri=2.2, call_gfri=0.5)
    p1 = evaluate_shadow_policy(
        c1,
        compute_near_term_ref(near_hist + [c0], c1),
        compute_long_term_ref(long_hist, c1),
        prior_state=state,
    )
    assert p1["shadow_decision"] == "SHADOW_PAUSE_PUT"
    assert "HYSTERESIS_HOLD_ABOVE_RELEASE" in p1["candidate_reason_codes"]["put"]
    state = p1["next_policy_state"]

    # Cycle 2: 1.8x (< 2.0x) first cycle -> still holds pending 2nd release cycle
    c2 = _make_cycle(now + 20 * 60_000, put_gfri=1.8, call_gfri=0.5)
    p2 = evaluate_shadow_policy(
        c2,
        compute_near_term_ref(near_hist + [c0, c1], c2),
        compute_long_term_ref(long_hist, c2),
        prior_state=state,
    )
    assert p2["shadow_decision"] == "SHADOW_PAUSE_PUT"
    assert p2["next_policy_state"]["put"]["release_consecutive"] == 1
    state = p2["next_policy_state"]

    # Cycle 3: 1.7x (< 2.0x) second consecutive cycle -> releases to NO_VETO
    c3 = _make_cycle(now + 30 * 60_000, put_gfri=1.7, call_gfri=0.5)
    p3 = evaluate_shadow_policy(
        c3,
        compute_near_term_ref(near_hist + [c0, c1, c2], c3),
        compute_long_term_ref(long_hist, c3),
        prior_state=state,
    )
    assert p3["shadow_decision"] == "NO_VETO"
    assert "HYSTERESIS_RELEASED" in p3["candidate_reason_codes"]["put"]


def test_shadow_ledger_append_only_and_malformed_tolerance(tmp_path: Path) -> None:
    ledger_path = tmp_path / "shadow_ledger.jsonl"
    ledger = ShadowLedger(ledger_path)

    rec1 = _make_cycle(BASE_TS_MS - 60_000, put_gfri=1.1)
    rec2 = _make_cycle(BASE_TS_MS - 30_000, put_gfri=1.4)
    ledger.append_record(rec1)
    first_bytes = ledger_path.read_bytes()

    ledger.append_record(rec2)
    second_bytes = ledger_path.read_bytes()
    # Strictly append-only: existing prefix bytes are byte-for-byte identical
    assert second_bytes.startswith(first_bytes)
    assert len(second_bytes) > len(first_bytes)

    # Inject a corrupted JSON line and a non-dict JSON line without touching valid lines
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write("{corrupted_json_line\n")
        fh.write("[1, 2, 3]\n")
    rec3 = _make_cycle(BASE_TS_MS - 10_000, put_gfri=1.6)
    ledger.append_record(rec3)

    loaded = ShadowLedger.load_records(ledger_path)
    assert [r["freeze_at_ms"] for r in loaded] == [
        rec1["freeze_at_ms"],
        rec2["freeze_at_ms"],
        rec3["freeze_at_ms"],
    ]
    assert [r["freeze_at_ms"] for r in ledger.load_records()] == [
        rec1["freeze_at_ms"],
        rec2["freeze_at_ms"],
        rec3["freeze_at_ms"],
    ]


def test_audit_record_and_minimal_console_renderer() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=1.0, call_gfri=0.5)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.5)
    current = _make_cycle(now, put_gfri=2.48, call_gfri=0.0)

    near_ref = compute_near_term_ref(near_hist, current)
    long_ref = compute_long_term_ref(long_hist, current)
    policy = evaluate_shadow_policy(current, near_ref, long_ref)
    audit = build_audit_record(current, near_ref, long_ref, policy, git_commit="abc1234")

    # Verify required Section 8 audit fields and valid JSON roundtrip
    required_keys = {
        "schema",
        "code_version",
        "git_commit",
        "decision_authority",
        "live_veto_enabled",
        "player_integration_enabled",
        "config_snapshot",
        "raw_input_hashes",
        "units",
        "timestamps",
        "hedge_flows",
        "order_book",
        "ratios",
        "cross_venue_ref",
        "near_ref",
        "long_ref",
        "policy_version",
        "candidate_reason_codes",
        "data_quality",
        "failure_reasons",
    }
    assert required_keys.issubset(audit.keys())
    assert audit["schema"] == SCHEMA_AUDIT
    assert audit["decision_authority"] == "SHADOW_ONLY"
    assert audit["live_veto_enabled"] is False
    assert audit["player_integration_enabled"] is False
    assert "sensitive" in policy["sensitivity_comparison"]["presets"]
    assert "baseline" in policy["sensitivity_comparison"]["presets"]
    assert "conservative" in policy["sensitivity_comparison"]["presets"]
    json.dumps(audit, allow_nan=False)

    console = render_minimal_console(audit)
    lines = console.splitlines()
    assert len(lines) <= 12
    assert "GFRI v1.1                 SHADOW" in lines[0]
    assert "PUT  下行压力  +2.48x" in console
    assert "CALL 上行压力  +0.00x (阻尼)" in console
    assert "状态：不阻断（候选）" in console
    assert "数据质量：VALID" in console

    forbidden_terms = [
        "Gamma墙",
        "Gamma wall",
        "Pin",
        "pinning",
        "钉住",
        "承保",
        "underwriting",
    ]
    for term in forbidden_terms:
        assert term.lower() not in console.lower()


def test_locked_shadow_only_enforcement_raises_on_tampered_flags() -> None:
    now = BASE_TS_MS
    current = _make_cycle(now, put_gfri=1.0, call_gfri=0.5)
    near_ref = compute_near_term_ref([], current)
    long_ref = compute_long_term_ref([], current)
    valid_policy = evaluate_shadow_policy(current, near_ref, long_ref)

    tampered_configs = [
        replace(DEFAULT_CONFIG, flags=ShadowFlags(decision_authority="LIVE")),
        replace(DEFAULT_CONFIG, flags=ShadowFlags(live_veto_enabled=True)),
        replace(DEFAULT_CONFIG, flags=ShadowFlags(player_integration_enabled=True)),
    ]
    for bad_cfg in tampered_configs:
        assert isinstance(bad_cfg, GfriConfig)
        with pytest.raises(ValueError):
            evaluate_shadow_policy(current, near_ref, long_ref, cfg=bad_cfg)
        with pytest.raises(ValueError):
            build_audit_record(current, near_ref, long_ref, valid_policy, cfg=bad_cfg)


def test_warmup_policy_guard_rapid_deterioration_and_audit_ledger_roundtrip(tmp_path: Path) -> None:
    now = BASE_TS_MS
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.5)

    # Cold-start warmup prevents firing on single unconfirmed spike
    curr_spike = _make_cycle(now, put_gfri=4.0, call_gfri=0.5)
    near_warm = compute_near_term_ref([], curr_spike)
    long_ready = compute_long_term_ref(long_hist, curr_spike)
    pol_warm = evaluate_shadow_policy(curr_spike, near_warm, long_ready)
    assert pol_warm["shadow_decision"] == "WARMING_UP"

    # Rapid deterioration: >= 2.0x and delta_ratio >= 1.50x with CONFIRMED
    near_hist = _make_ready_near_history(now, put_gfri=1.0, call_gfri=0.5)
    curr_deteriorate = _make_cycle(now, put_gfri=2.1, call_gfri=0.5)
    near_ready = compute_near_term_ref(near_hist, curr_deteriorate)
    pol_det = evaluate_shadow_policy(curr_deteriorate, near_ready, long_ready)
    assert pol_det["shadow_decision"] == "SHADOW_PAUSE_PUT"
    assert "RAPID_DETERIORATION_TRIGGER" in pol_det["candidate_reason_codes"]["put"]

    # Audit record stored in ShadowLedger can be fed directly back into history refs
    ledger = ShadowLedger(tmp_path / "audit_ledger.jsonl")
    for idx in range(4):
        c_i = _make_cycle(now - (4 - idx) * 30 * 60_000, put_gfri=1.0, call_gfri=0.5)
        n_i = compute_near_term_ref([], c_i)
        l_i = compute_long_term_ref([], c_i)
        p_i = evaluate_shadow_policy(c_i, n_i, l_i)
        ledger.append_record(build_audit_record(c_i, n_i, l_i, p_i))

    loaded_audits = ledger.load_records()
    near_from_audits = compute_near_term_ref(loaded_audits, curr_deteriorate)
    assert near_from_audits["put"]["state"] == "READY"
    assert near_from_audits["put"]["sample_count"] == 4


def test_cross_side_blip_does_not_suppress_confirmed_opposite_pause() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=1.0, call_gfri=2.8)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=1.0)

    # PUT had a single transient blip while CALL has sustained CONFIRMED high GFRI
    cycle = _make_cycle(
        now,
        put_gfri=1.1,
        call_gfri=3.1,
        confirmation_status="SINGLE_BLIP_FILTERED",
        put_single_blip=True,
        call_single_blip=False,
    )
    cycle["put"]["confirmation_status"] = "SINGLE_BLIP_FILTERED"
    cycle["call"]["confirmation_status"] = "CONFIRMED"

    near_ref = compute_near_term_ref(near_hist, cycle)
    long_ref = compute_long_term_ref(long_hist, cycle)
    prior_state = {
        "put": {"active_pause": False, "pause_consecutive": 0, "release_consecutive": 0},
        "call": {"active_pause": False, "pause_consecutive": 1, "release_consecutive": 0},
    }
    pol = evaluate_shadow_policy(cycle, near_ref, long_ref, prior_state=prior_state)
    assert pol["put_decision"] == "NO_VETO"
    assert pol["call_decision"] == "SHADOW_PAUSE"
    assert pol["shadow_decision"] == "SHADOW_PAUSE_CALL"


def test_p02_side_independence_during_partial_unknown_and_audit_archive() -> None:
    now = BASE_TS_MS
    near_hist = _make_ready_near_history(now, put_gfri=2.3, call_gfri=2.3)
    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=1.0)

    # PUT truncated (UNKNOWN) while global_data_quality is VALID and CALL is VALID at 3.0769x
    cycle = _make_cycle(
        now,
        put_gfri=None,
        call_gfri=3.0769,
        data_quality="UNKNOWN",
        put_status="UNKNOWN",
        call_status="VALID",
    )
    cycle["global_data_quality"] = "VALID"
    cycle["partial_valid_side"] = "CALL"

    near_ref = compute_near_term_ref(near_hist, cycle)
    long_ref = compute_long_term_ref(long_hist, cycle)

    # 1st spike on CALL -> CALL preserves NO_VETO (not coerced to UNKNOWN), PUT is UNKNOWN, top-level UNKNOWN
    pol_first = evaluate_shadow_policy(cycle, near_ref, long_ref)
    assert pol_first["put_decision"] == "UNKNOWN"
    assert pol_first["call_decision"] == "NO_VETO"
    assert pol_first["shadow_decision"] == "UNKNOWN"
    assert pol_first["partial_valid_side"] == "CALL"
    assert pol_first["partial_pause_side"] is None

    # Sustained spike on CALL -> CALL triggers SHADOW_PAUSE while PUT remains UNKNOWN
    prior_sustained = {
        "put": {"active_pause": False, "pause_consecutive": 0, "release_consecutive": 0},
        "call": {"active_pause": False, "pause_consecutive": 1, "release_consecutive": 0},
    }
    pol_sustained = evaluate_shadow_policy(cycle, near_ref, long_ref, prior_state=prior_sustained)
    assert pol_sustained["put_decision"] == "UNKNOWN"
    assert pol_sustained["call_decision"] == "SHADOW_PAUSE"
    assert pol_sustained["shadow_decision"] == "UNKNOWN"
    assert pol_sustained["partial_valid_side"] == "CALL"
    assert pol_sustained["partial_pause_side"] == "CALL"
    assert pol_sustained["next_policy_state"]["last_freeze_at_ms"] == now

    # Audit record includes clock_role, cycle_asof_ms, global_data_quality, partial_valid_side, raw_archive_status
    audit_failed_archive = build_audit_record(
        cycle,
        near_ref,
        long_ref,
        pol_sustained,
        raw_archive_status={
            "status": "FAILED",
            "complete_replayable": True,  # must be sanitized to False
            "errors": ["disk_full_on_raw_capture"],
            "files": {},
        },
    )
    assert audit_failed_archive["cycle_asof_ms"] == now
    assert audit_failed_archive["clock_role"] == "SHADOW_HIGH_FREQ_CYCLE"
    assert audit_failed_archive["global_data_quality"] == "VALID"
    assert audit_failed_archive["partial_valid_side"] == "CALL"
    assert audit_failed_archive["raw_archive_status"]["complete_replayable"] is False
    assert "disk_full_on_raw_capture" in audit_failed_archive["failure_reasons"]

    # If global_data_quality itself is UNKNOWN, both sides must be UNKNOWN
    cycle_global_bad = dict(cycle, global_data_quality="UNKNOWN")
    pol_global_bad = evaluate_shadow_policy(cycle_global_bad, near_ref, long_ref)
    assert pol_global_bad["put_decision"] == "UNKNOWN"
    assert pol_global_bad["call_decision"] == "UNKNOWN"
    assert pol_global_bad["shadow_decision"] == "UNKNOWN"


def test_p06_bounded_memory_index_and_bit_identical_refs(tmp_path: Path) -> None:
    now = BASE_TS_MS
    ledger_path = tmp_path / "gfri_shadow_ledger.jsonl"
    padding_blob = "x" * 16_500  # ~17KB+ per serialized audit record (~150MB total for 8,640 lines)

    total_records = 8_640  # 60 days at 10-minute intervals
    start_ts = now - total_records * 10 * 60_000

    # Write 8,640 realistic heavyweight gfri_audit_v11-sized JSONL lines directly
    template_cycle = _make_cycle(start_ts, put_gfri=1.2, call_gfri=0.6)
    template_cycle["research_submetrics"] = {"padding": padding_blob}
    sample_index = extract_index_entry(template_cycle)
    assert set(sample_index.keys()) == {
        "schema",
        "freeze_at_ms",
        "data_quality",
        "global_data_quality",
        "comparability",
        "put",
        "call",
    }
    assert sample_index["schema"] == INDEX_SCHEMA
    assert len(json.dumps(sample_index, separators=(",", ":"))) < 600

    with ledger_path.open("w", encoding="utf-8") as fh:
        for i in range(total_records):
            ts = start_ts + i * 10 * 60_000
            put_val = 1.0 + (i % 5) * 0.25
            call_val = 0.5 + (i % 4) * 0.15
            rec = {
                "schema": SCHEMA_AUDIT,
                "freeze_at_ms": ts,
                "data_quality": "VALID",
                "global_data_quality": "VALID",
                "comparability": template_cycle["comparability"],
                "put": {
                    "status": "VALID",
                    "gfri_confirmed": put_val,
                    "liquidity_depth_usd": 1_000_000.0,
                    "raw_hedge_flow_usd": -(put_val * 1_000_000.0),
                },
                "call": {
                    "status": "VALID",
                    "gfri_confirmed": call_val,
                    "liquidity_depth_usd": 1_000_000.0,
                    "raw_hedge_flow_usd": -(call_val * 1_000_000.0),
                },
                "config_snapshot": DEFAULT_CONFIG.to_audit_dict(),
                "sensitivity_comparison": {"presets": {"baseline": {"shadow_decision": "NO_VETO"}}},
                "sample_refs": [{"sample_idx": 0, "ob_sha256": "abc", "raw_blob": padding_blob}],
            }
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    assert ledger_path.stat().st_size > 140_000_000

    # Measure peak Python traced memory when streaming via load_history_window
    tracemalloc.start()
    try:
        window_entries = ShadowLedger.load_history_window(
            ledger_path,
            reference_ms=now,
            max_age_ms=60 * MS_PER_DAY,
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(window_entries) == total_records
    assert peak_bytes < 32 * 1024 * 1024  # well under 32 MiB budget (vs 128M systemd cap)

    # Verify companion index creation & bit-identical near/long ref vs full load_records
    small_ledger = ShadowLedger(tmp_path / "compact_test" / "gfri_shadow_ledger.jsonl")
    for idx in range(20):
        ts = now - (20 - idx) * 30 * 60_000
        small_ledger.append_record(_make_cycle(ts, put_gfri=1.0 + idx * 0.05, call_gfri=0.6))

    assert small_ledger.index_path.exists()
    curr = _make_cycle(now, put_gfri=2.1, call_gfri=0.7)
    full_recs = small_ledger.load_records()
    indexed_recs = small_ledger.load_history_window(reference_ms=now, max_age_ms=60 * MS_PER_DAY)

    assert compute_near_term_ref(indexed_recs, curr) == compute_near_term_ref(full_recs, curr)
    assert compute_long_term_ref(indexed_recs, curr) == compute_long_term_ref(full_recs, curr)


def test_p11_crash_recovery_from_ledger_and_stale_lock_recovery(tmp_path: Path) -> None:
    now = BASE_TS_MS
    ledger_path = tmp_path / "gfri_shadow_ledger.jsonl"
    ledger = ShadowLedger(ledger_path, lock_timeout_sec=0.25, stale_lock_timeout_ms=500)

    long_hist = _make_ready_long_history(now, days=35, samples_per_day=2, put_gfri=1.0, call_gfri=0.5)
    near_hist = _make_ready_near_history(now, put_gfri=1.0, call_gfri=0.5)

    # Cycle 1: first spike -> pause_consecutive becomes 1
    c1 = _make_cycle(now - 10 * 60_000, put_gfri=2.7, call_gfri=0.5)
    n1 = compute_near_term_ref(near_hist, c1)
    l1 = compute_long_term_ref(long_hist, c1)
    p1 = evaluate_shadow_policy(c1, n1, l1)
    a1 = build_audit_record(c1, n1, l1, p1)
    ledger.append_record(a1)

    # Simulate crash BEFORE gfri_policy_state.json is written: recover state solely from ledger
    recovered = reconstruct_policy_state_from_ledger(ledger.load_records())
    assert recovered is not None
    assert recovered["last_freeze_at_ms"] == c1["freeze_at_ms"]
    assert recovered["put"]["pause_consecutive"] == 1

    # Cycle 2 using recovered state triggers SHADOW_PAUSE_PUT cleanly
    c2 = _make_cycle(now, put_gfri=2.8, call_gfri=0.5)
    n2 = compute_near_term_ref(near_hist + [c1], c2)
    l2 = compute_long_term_ref(long_hist, c2)
    p2 = evaluate_shadow_policy(c2, n2, l2, prior_state=recovered)
    assert p2["put_decision"] == "SHADOW_PAUSE"
    assert p2["shadow_decision"] == "SHADOW_PAUSE_PUT"

    # Simulate stale lockfile older than stale_lock_timeout_ms (500ms)
    stale_ms = int(time.time() * 1000) - 5_000
    ledger.lock_path.write_text(
        json.dumps({"pid": os.getpid(), "created_at_ms": stale_ms}),
        encoding="utf-8",
    )
    old_sec = time.time() - 5.0
    os.utime(ledger.lock_path, (old_sec, old_sec))

    # append_record must recover the stale lock instead of timing out
    a2 = build_audit_record(c2, n2, l2, p2)
    ledger.append_record(a2)
    assert len(ledger.load_records()) == 2


def test_p12_select_snapshot_asof_episode_excludes_future_and_honors_archive() -> None:
    t_1350 = 1_760_017_800_000  # 13:50 UTC
    t_1400 = 1_760_018_400_000  # 14:00 UTC (episode freeze)
    t_1410 = 1_760_019_000_000  # 14:10 UTC (post-freeze)

    records = [
        {
            "freeze_at_ms": t_1350,
            "id": "snap_1350_replayable",
            "raw_archive_status": {"status": "COMPLETE", "complete_replayable": True},
        },
        {
            "freeze_at_ms": t_1400,
            "id": "snap_1400_non_replayable",
            "raw_archive_status": {"status": "PARTIAL", "complete_replayable": False},
        },
        {
            "freeze_at_ms": t_1410,
            "id": "snap_1410_future",
            "raw_archive_status": {"status": "COMPLETE", "complete_replayable": True},
        },
    ]

    picked_any = select_snapshot_asof_episode(records, t_1400)
    assert picked_any is not None
    assert picked_any["id"] == "snap_1400_non_replayable"

    picked_replayable = select_snapshot_asof_episode(
        records,
        t_1400,
        require_replayable_archive=True,
    )
    assert picked_replayable is not None
    assert picked_replayable["id"] == "snap_1350_replayable"
    assert picked_replayable["freeze_at_ms"] <= t_1400

    assert select_snapshot_asof_episode(records, t_1350 - 1) is None
