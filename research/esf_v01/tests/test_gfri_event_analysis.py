"""Unit and integration tests for research.esf_v01.gfri_event_analysis."""

from __future__ import annotations

import copy
import pytest

from research.esf_v01.gfri_event_analysis import (
    compute_all_distributions,
    compute_runtime_audit,
    extract_price_blind_anomalies,
    load_dataset,
    run_full_analysis,
    scan_major_btc_moves,
)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


@pytest.fixture(scope="module")
def full_results():
    return run_full_analysis()


def test_a0_exact_cycle_and_integrity_counts(full_results):
    ov = full_results["runtime_audit"]["overall"]

    assert ov["total_cycles"] == 755
    assert ov["first_freeze_iso"] == "2026-11-18T18:10:27Z"
    assert ov["last_freeze_iso"] == "2026-11-24T07:47:43Z"

    # True systemd cadence (~638s / 10.63m -> ~135.4 cycles/day vs naive 144)
    assert ov["observed_median_cadence_sec"] == pytest.approx(638.0, abs=0.5)
    assert ov["expected_cycles_per_full_day"] == pytest.approx(135.42, abs=0.2)
    assert ov["inter_cycle_intervals_all_sec"]["count"] == 754
    assert ov["inter_cycle_intervals_regular_sec"]["count"] == 753
    assert len(ov["upgrade_restart_intervals"]) == 1
    assert ov["upgrade_restart_intervals"][0]["interval_sec"] == pytest.approx(178.0, abs=0.5)

    # Git commits
    assert ov["git_commit_counts"] == {"f3e565e48d59": 3, "855ff4ba593f": 752}

    # Ledger & raw SHA-256 capture integrity
    assert ov["ledger_consistency"]["consistent"] is True
    assert ov["ledger_consistency"]["ledger_count"] == 755
    assert ov["ledger_consistency"]["index_count"] == 755
    assert ov["raw_captures_hash_audit"]["total_raw_files_hashed"] == 6730
    assert ov["raw_captures_hash_audit"]["total_raw_bytes_hashed"] == 679_243_431
    assert ov["raw_captures_hash_audit"]["sha256_mismatch_count"] == 0
    assert ov["raw_captures_hash_audit"]["missing_file_count"] == 0

    # Archive & replay split
    assert ov["raw_archive_status_counts"] == {"SAVED": 732, "PARTIAL": 23, "FAILED": 0}
    assert ov["replay_class_counts"] == {"REPLAYABLE": 732, "NON_REPLAYABLE": 23}
    assert ov["partial_breakdown"]["api_outage_fail_closed_unknown"] == 11
    assert ov["partial_breakdown"]["single_book_hiccup_2of3_numeric"] == 12

    # Critical VALID + DEGRADED numerical retention (696 + 44 = 740; 15 null)
    assert ov["numerical_cycles_total"] == 740
    assert ov["put_quality_breakdown"]["valid_numeric"] == 696
    assert ov["put_quality_breakdown"]["degraded_numeric"] == 44
    assert ov["put_quality_breakdown"]["degraded_null"] == 1
    assert ov["put_quality_breakdown"]["unknown_null"] == 14
    assert ov["put_quality_breakdown"]["null_total"] == 15
    assert ov["global_data_quality_counts"] == {"VALID": 0, "DEGRADED": 740, "UNKNOWN": 15}
    assert ov["numerical_with_secondary_venue_unavailable"] == 740

    # Primary method & GEX comparability stability
    assert ov["primary_method_counts"] == {"HEDGE_FLOW_NATIVE": 745, "NET_GEX_LINEAR": 0, "UNKNOWN": 10}
    assert ov["comparability_gex_valid_unique_count"] == 1
    assert ov["upstream_gex_stability"]["consecutive_duplicate_snapshots_numerical"] == 0
    assert ov["upstream_gex_stability"]["instrument_count_min"] == 759
    assert ov["upstream_gex_stability"]["instrument_count_max"] == 891


def test_degraded_numerical_cycles_retained_in_distributions_and_events(full_results):
    daily_by_id = {d["slice_id"]: d for d in full_results["distributions"]["daily"]}
    assert daily_by_id["DAY_2026-11-21"]["numerical_cycles"] == 134
    assert daily_by_id["DAY_2026-11-21"]["put_valid_count"] == 122
    assert daily_by_id["DAY_2026-11-21"]["put_degraded_numeric_count"] == 12
    assert daily_by_id["DAY_2026-11-21"]["put_dist"]["max"] == pytest.approx(6.035451, rel=1e-5)

    assert daily_by_id["DAY_2026-11-23"]["numerical_cycles"] == 136
    assert daily_by_id["DAY_2026-11-23"]["put_valid_count"] == 127
    assert daily_by_id["DAY_2026-11-23"]["put_degraded_numeric_count"] == 9

    events_by_iso = {e["onset_iso"]: e for e in full_results["anomalies"]["events"]}
    # Key fast-move DEGRADED events must be retained with status explicitly preserved
    assert "2026-11-21T06:19:51Z" in events_by_iso
    e_1121_v = events_by_iso["2026-11-21T06:19:51Z"]
    assert e_1121_v["onset_put_status"] == "DEGRADED"
    assert e_1121_v["peak_put_gfri"] == pytest.approx(6.035451, rel=1e-5)
    assert e_1121_v["peak_call_gfri"] == pytest.approx(5.784858, rel=1e-5)

    assert "2026-11-23T14:33:55Z" in events_by_iso
    e_1123_top = events_by_iso["2026-11-23T14:33:55Z"]
    assert e_1123_top["peak_put_status"] == "DEGRADED"
    assert e_1123_top["peak_put_gfri"] == pytest.approx(4.149174, rel=1e-5)

    assert "2026-11-23T16:41:45Z" in events_by_iso
    e_1123_breach = events_by_iso["2026-11-23T16:41:45Z"]
    assert e_1123_breach["onset_put_status"] == "DEGRADED"
    assert e_1123_breach["onset_put_gfri"] == pytest.approx(2.642717, rel=1e-5)


def test_step_log_decomposition_identity_and_depth_dominance(full_results):
    all_slices = (
        [full_results["distributions"]["overall"]]
        + full_results["distributions"]["daily"]
        + full_results["distributions"]["episodes"]
    )
    for sl in all_slices:
        for side_key in ("log_variance_put", "log_variance_call"):
            lv = sl[side_key]
            assert lv["max_identity_abs_error"] < 1e-12
            assert lv["max_3sample_raw_depth_abs_error"] < 1e-12
            if lv["depth_cov_share"] is not None and lv["hf_cov_share"] is not None:
                assert (lv["depth_cov_share"] + lv["hf_cov_share"]) == pytest.approx(1.0, abs=1e-9)

    # Across all natural days, Deribit order book depth explains 90%–100% of 10m step variance
    for d in full_results["distributions"]["daily"]:
        assert 0.89 <= d["log_variance_put"]["depth_unsplit_var_share"] <= 1.0
        assert 0.89 <= d["log_variance_call"]["depth_unsplit_var_share"] <= 1.0


def test_non_lookahead_event_windows_and_price_blindness(dataset):
    cycles = dataset["cycles"]
    btc_points = dataset["market_paths"]["btc_1m_points"]

    base = extract_price_blind_anomalies(cycles, btc_points)
    for ev in base["events"]:
        t0 = ev["onset_ms"]
        if ev["pre_120m_asof_ms"] is not None:
            assert ev["pre_120m_asof_ms"] <= t0 - 7_200_000
        if ev["pre_60m_asof_ms"] is not None:
            assert ev["pre_60m_asof_ms"] <= t0 - 3_600_000
        if ev["pre_30m_asof_ms"] is not None:
            assert ev["pre_30m_asof_ms"] <= t0 - 1_800_000
        assert ev["btc_matched_ts_ms"] <= t0

    # Price-blindness check: scaling all BTC prices by 10x or scrambling post-event BTC returns
    # must produce the exact same ex-ante event timestamps, cluster counts, and pre-event GFRI values.
    perturbed_btc = [[row[0], float(row[1]) * (1.0 + 0.25 * (i % 3)), row[2]] for i, row in enumerate(btc_points)]
    perturbed = extract_price_blind_anomalies(cycles, perturbed_btc)
    assert [e["onset_iso"] for e in perturbed["events"]] == [e["onset_iso"] for e in base["events"]]
    assert [e["pre_60m_put"] for e in perturbed["events"]] == [e["pre_60m_put"] for e in base["events"]]

    # Truncation non-lookahead check: truncating future cycles after 2026-11-21T02:20:00Z must not
    # alter the ex-ante values of the 2026-11-21T02:04:27Z event.
    cutoff_cycles = [c for c in copy.deepcopy(cycles) if c["freeze_iso"] <= "2026-11-21T02:20:00Z"]
    trunc_res = extract_price_blind_anomalies(cutoff_cycles, btc_points)
    ev_trunc = next(e for e in trunc_res["events"] if e["onset_iso"] == "2026-11-21T02:04:27Z")
    ev_full = next(e for e in base["events"] if e["onset_iso"] == "2026-11-21T02:04:27Z")
    for field in ("onset_put_gfri", "onset_call_gfri", "pre_120m_put", "pre_60m_put", "pre_30m_put", "btc_price_t0"):
        assert ev_trunc[field] == ev_full[field]


def test_positive_and_counterexample_classifications_present(full_results):
    events_by_iso = {e["onset_iso"]: e for e in full_results["anomalies"]["events"]}

    # Genuine LEADING liquidity withdrawal on 2026-11-21
    ev_lead = events_by_iso["2026-11-21T02:04:27Z"]
    assert ev_lead["outcome_classification"] == "LEADING"
    assert ev_lead["onset_put_gfri"] == pytest.approx(5.212896, rel=1e-5)
    assert ev_lead["btc_post120m_min_pct"] < -4.0

    # Extreme Deribit-only quote-pull FALSE_POSITIVE counterexamples
    assert events_by_iso["2026-11-20T09:03:23Z"]["outcome_classification"] == "FALSE_POSITIVE"
    assert events_by_iso["2026-11-21T20:31:38Z"]["outcome_classification"] == "FALSE_POSITIVE"
    assert events_by_iso["2026-11-23T06:24:05Z"]["outcome_classification"] == "FALSE_POSITIVE"
    assert events_by_iso["2026-11-23T10:29:13Z"]["outcome_classification"] == "FALSE_POSITIVE"
    assert events_by_iso["2026-11-23T22:01:25Z"]["outcome_classification"] == "FALSE_POSITIVE"

    # LAGGING & COINCIDENT classifications
    assert events_by_iso["2026-11-20T16:08:29Z"]["outcome_classification"] == "LAGGING"
    assert events_by_iso["2026-11-21T06:19:51Z"]["outcome_classification"] == "COINCIDENT"
    assert events_by_iso["2026-11-23T14:33:55Z"]["outcome_classification"] == "COINCIDENT"

    # Reverse BTC move scan must include LEADING, COINCIDENT, LAGGING, NO_SIGNAL, and INCONCLUSIVE
    m_counts = full_results["move_scan"]["classification_counts"]
    for label in ("LEADING", "COINCIDENT", "LAGGING", "NO_SIGNAL", "INCONCLUSIVE"):
        assert m_counts[label] >= 1

    moves = full_results["move_scan"]["moves"]
    # 2026-11-21 crash core leg classified LEADING
    m_1121_crash = next(m for m in moves if m["onset_iso"].startswith("2026-11-21T03:15"))
    assert m_1121_crash["classification"] == "LEADING"

    # 2026-11-23 +5.44% rally and 16:13Z 62,000-P breach leg classified NO_SIGNAL
    m_1123_rally = next(m for m in moves if m["onset_iso"].startswith("2026-11-23T13:55"))
    assert m_1123_rally["classification"] == "NO_SIGNAL"
    m_1123_breach = next(m for m in moves if m["onset_iso"].startswith("2026-11-23T16:13"))
    assert m_1123_breach["classification"] == "NO_SIGNAL"
