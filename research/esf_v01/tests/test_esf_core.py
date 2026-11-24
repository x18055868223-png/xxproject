"""Unit and empirical reproduction tests for ESF v0.1 (esf_core & reproduce_study)."""
from __future__ import annotations

import math
from pathlib import Path
import re

import pytest

from research.esf_v01 import esf_core
from research.esf_v01.reproduce_study import (
    STUDY_FINAL_VERDICT,
    format_study_report,
    load_study_inputs,
    run_full_study,
)


def test_authority_and_calibration_constants() -> None:
    assert esf_core.ESF_VERSION == "esf_v0.1"
    assert esf_core.DECISION_AUTHORITY == "RESEARCH_ONLY"
    assert esf_core.LIVE_VETO_ENABLED is False
    assert esf_core.PLAYER_INTEGRATION_ENABLED is False
    assert esf_core.PARAM_CALIBRATION_STATUS == "UNVALIDATED_CANDIDATE_PARAMS"


def test_short_leg_log_distance_and_already_breached_flag() -> None:
    put_otm = esf_core.compute_short_leg_distance("put", 62789.69, 62000.0)
    assert put_otm["boundary_state"] == "OTM_CUSHION"
    assert put_otm["distance_pct"] == pytest.approx(1.26564, abs=1e-4)

    # Real 2026-11-17 16:02Z Call 64000-C at S=64,093.32 -> D_call = -0.15%
    call_itm = esf_core.compute_short_leg_distance("call", 64093.32, 64000.0)
    assert call_itm["boundary_state"] == "ALREADY_BREACHED"
    assert call_itm["distance_pct"] == pytest.approx(-0.1457, abs=1e-3)

    exact_atm = esf_core.compute_short_leg_distance("put", 60000.0, 60000.0)
    assert exact_atm["distance_log"] == 0.0
    assert exact_atm["boundary_state"] == "ALREADY_BREACHED"


def test_rv_relative_standard_errors() -> None:
    assert esf_core.rv_relative_standard_error(24) == pytest.approx(0.14744, abs=1e-4)
    assert esf_core.rv_relative_standard_error(168) == pytest.approx(0.05472, abs=1e-4)
    assert esf_core.rv_relative_standard_error(48) == pytest.approx(0.10314, abs=1e-4)
    assert esf_core.rv_relative_standard_error(288) == pytest.approx(0.04174, abs=1e-4)


def test_5m_realized_vol_strict_no_lookahead_and_gap_fail_closed() -> None:
    base_ts = 1_795_449_600_000  # exact 5m boundary
    pts = []
    price = 60000.0
    for k in range(60, -1, -1):
        ts = base_ts - k * esf_core.FIVE_MIN_MS
        step = 0.002 if k % 2 == 0 else -0.0015
        price = price * math.exp(step)
        pts.append([ts, price])

    asof_ms = base_ts + 120_000
    rv_clean = esf_core.compute_5m_realized_vol(pts, asof_ms=asof_ms, window_hours=4.0)
    assert rv_clean is not None and rv_clean > 0.0

    # Injecting massive future shock strictly after asof_ms MUST NOT alter RV
    pts_with_future = list(pts) + [
        [base_ts + 300_000, price * 0.50],
        [base_ts + 600_000, price * 1.80],
    ]
    rv_with_future = esf_core.compute_5m_realized_vol(pts_with_future, asof_ms=asof_ms, window_hours=4.0)
    assert rv_with_future == pytest.approx(rv_clean, rel=0.0, abs=1e-12)

    # Deleting an interior 5m observation must fail closed to None rather than stretch returns
    gapped = [row for row in pts if row[0] != base_ts - 5 * esf_core.FIVE_MIN_MS]
    assert esf_core.compute_5m_realized_vol(gapped, asof_ms=asof_ms, window_hours=4.0) is None


def test_align_gfri_strict_no_lookahead_and_max_age() -> None:
    cycles = [
        {
            "freeze_at_ms": 1_000_000,
            "freeze_iso": "t1",
            "put": {"status": "VALID", "gfri_confirmed": 2.2},
            "call": {"status": "VALID", "gfri_confirmed": 2.1},
        },
        {
            "freeze_at_ms": 1_500_000,
            "freeze_iso": "t2_future_by_1ms",
            "put": {"status": "VALID", "gfri_confirmed": 9.9},
            "call": {"status": "VALID", "gfri_confirmed": 9.9},
        },
    ]
    # 1ms before t2 -> must return t1, never t2
    aligned = esf_core.align_gfri_no_lookahead(cycles, 1_499_999, "put", max_age_ms=1_200_000)
    assert aligned is not None
    assert aligned["freeze_at_ms"] == 1_000_000
    assert aligned["record"]["put"]["gfri_confirmed"] == 2.2

    # Exceeding max_age_ms past latest cycle (1_500_000 + 1_200_001) -> returns None
    stale = esf_core.align_gfri_no_lookahead(cycles, 2_700_001, "put", max_age_ms=1_200_000)
    assert stale is None

    # UNKNOWN or null gfri_confirmed is skipped
    cycles_unknown = [
        {
            "freeze_at_ms": 1_400_000,
            "put": {"status": "UNKNOWN", "gfri_confirmed": None},
        }
    ]
    assert esf_core.align_gfri_no_lookahead(cycles_unknown, 1_450_000, "put") is None


def test_feedback_state_cross_venue_flicker_vs_corroborated_surge() -> None:
    flicker_rec = {
        "freeze_iso": "2026-11-20T16:19:06Z",
        "put": {"status": "VALID", "gfri_confirmed": 8.164, "liquidity_depth_usd": 13_449_750.0, "raw_hedge_flow_usd": -109_800_000.0},
        "near_ref": {"delta_ratio_put": 1.864, "driver_put": "LIQUIDITY_DROP"},
        "okx_ref": {"ratio_okx_to_deribit_down": 6.82, "gfri_down_1pct_okx": 1.196, "down_usd": 91_784_068.0},
    }
    fb_flicker = esf_core.evaluate_feedback_state(flicker_rec, side="put", distance_log=0.0067)
    assert fb_flicker["deribit_quote_flicker_suspect"] is True
    assert fb_flicker["feedback_state"] == "QUOTE_FLICKER_SUSPECT"

    corroborated_rec = {
        "freeze_iso": "2026-11-21T02:04:27Z",
        "put": {"status": "VALID", "gfri_confirmed": 5.213, "liquidity_depth_usd": 22_386_230.0, "raw_hedge_flow_usd": -116_700_000.0},
        "near_ref": {"delta_ratio_put": 1.622, "driver_put": "LIQUIDITY_DROP"},
        "okx_ref": {"ratio_okx_to_deribit_down": 2.30, "gfri_down_1pct_okx": 2.262, "down_usd": 51_586_313.0},
    }
    fb_surge = esf_core.evaluate_feedback_state(corroborated_rec, side="put", distance_log=0.008)
    assert fb_surge["deribit_quote_flicker_suspect"] is False
    assert fb_surge["feedback_state"] == "AMPLIFYING_SURGE"
    assert fb_surge["spatial_relevance"] == "DIRECT_PROXIMITY"

    # Fail-closed guard: when okx_gfri is None, NEVER mute an amplifying surge as QUOTE_FLICKER_SUSPECT
    missing_okx_gfri_rec = {
        "freeze_iso": "2026-11-21T02:04:27Z",
        "put": {"status": "VALID", "gfri_confirmed": 10.0, "liquidity_depth_usd": 10_000_000.0, "raw_hedge_flow_usd": -100_000_000.0},
        "near_ref": {"delta_ratio_put": 1.80, "driver_put": "LIQUIDITY_DROP"},
        "okx_ref": {"ratio_okx_to_deribit_down": 3.00, "gfri_down_1pct_okx": None, "down_usd": 30_000_000.0},
    }
    fb_no_okx_gfri = esf_core.evaluate_feedback_state(missing_okx_gfri_rec, side="put", distance_log=0.008)
    assert fb_no_okx_gfri["deribit_quote_flicker_suspect"] is False
    assert fb_no_okx_gfri["feedback_state"] == "AMPLIFYING_SURGE"


def test_scheme1_invariants_thin_never_masked_and_wide_never_auto_rejected() -> None:
    # Invariant 1: Thin B_d < 0.50 sigma is ALWAYS '待校准 / 候选极高脆弱' even when GFRI is low (2.05x)
    low_gfri_fb = {
        "feedback_state": "NORMAL_OR_LOW",
        "spatial_relevance": "DIRECT_PROXIMITY",
    }
    s1_thin = esf_core.evaluate_scheme1_two_axis(
        distance_log=0.0127,
        buffer_sigma=0.320,
        feedback_info=low_gfri_fb,
    )
    assert s1_thin["candidate_status_cn"] == "待校准 / 候选极高脆弱"
    assert s1_thin["final_rank"] == 3

    # Invariant 2: Wide B_d >= 1.75 sigma with bilateral AMPLIFYING_SURGE upgrades at most +1 step
    surge_prox_fb = {
        "feedback_state": "AMPLIFYING_SURGE",
        "spatial_relevance": "MODERATE_PROXIMITY",
    }
    s1_wide_prox = esf_core.evaluate_scheme1_two_axis(
        distance_log=0.025,
        buffer_sigma=2.10,
        feedback_info=surge_prox_fb,
    )
    assert s1_wide_prox["base_rank"] == 0
    assert s1_wide_prox["final_rank"] == 1
    assert s1_wide_prox["candidate_status_cn"] == "待校准 / 候选中脆弱"

    # Invariant 3: Wide B_d >= 1.75 sigma and distant (>3% from +-1% shock) gets +0 upgrade
    surge_distant_fb = {
        "feedback_state": "AMPLIFYING_SURGE",
        "spatial_relevance": "DISTANT_FROM_1PCT_SHOCK",
    }
    s1_wide_dist = esf_core.evaluate_scheme1_two_axis(
        distance_log=0.055,
        buffer_sigma=2.10,
        feedback_info=surge_distant_fb,
    )
    assert s1_wide_dist["final_rank"] == 0
    assert s1_wide_dist["candidate_status_cn"] == "待校准 / 候选低脆弱"


def test_scheme2_strict_monotonicity() -> None:
    # Monotone increasing in D_d
    b_d1 = esf_core.evaluate_scheme2_stress_buffer(0.008, 0.03, 3.5)["buffer_stress_sigma"]
    b_d2 = esf_core.evaluate_scheme2_stress_buffer(0.016, 0.03, 3.5)["buffer_stress_sigma"]
    b_d3 = esf_core.evaluate_scheme2_stress_buffer(0.035, 0.03, 3.5)["buffer_stress_sigma"]
    assert b_d1 < b_d2 < b_d3

    # Monotone decreasing in V_T for D_d > 0
    b_v1 = esf_core.evaluate_scheme2_stress_buffer(0.015, 0.02, 3.0)["buffer_stress_sigma"]
    b_v2 = esf_core.evaluate_scheme2_stress_buffer(0.015, 0.04, 3.0)["buffer_stress_sigma"]
    assert b_v1 > b_v2

    # Monotone decreasing in GFRI_d for D_d > 0, GFRI_d > 1
    b_g1 = esf_core.evaluate_scheme2_stress_buffer(0.012, 0.03, 1.2)["buffer_stress_sigma"]
    b_g2 = esf_core.evaluate_scheme2_stress_buffer(0.012, 0.03, 3.0)["buffer_stress_sigma"]
    b_g3 = esf_core.evaluate_scheme2_stress_buffer(0.012, 0.03, 5.5)["buffer_stress_sigma"]
    assert b_g1 > b_g2 > b_g3


def test_empirical_reproduction_all_real_episodes_and_cards() -> None:
    inputs = load_study_inputs()
    study = run_full_study()

    assert study["final_verdict"] == STUDY_FINAL_VERDICT == "ESF_PARTIALLY_FEASIBLE"
    assert study["decision_authority"] == "RESEARCH_ONLY"
    assert study["live_veto_enabled"] is False
    assert study["player_integration_enabled"] is False

    c = study["counts"]
    assert c["official_dossiers_total"] == 10
    assert c["official_dealt_hands"] == 8
    assert c["official_data_blocked_hands"] == 2
    assert c["official_structures_total"] == 16
    assert c["official_shadow_locked_positions"] == 4
    assert c["official_counterfactual_structures"] == 12
    assert c["official_live_gfri_structures"] == 6
    assert c["official_pre_gfri_structures"] == 10
    assert c["gfri_cycles_total"] == 755
    assert c["unexecuted_archive_candidates_total"] == 33

    # Verify DATA_BLOCKED 11-22 & 11-23 expiries are preserved verbatim without fabrication
    blocked = {b["dossier"]: b for b in study["data_blocked_dossiers"]}
    assert set(blocked) == {"Deribit_BTC_20261122T080000Z", "Deribit_BTC_20261123T080000Z"}
    assert blocked["Deribit_BTC_20261122T080000Z"]["reason"] == "需同一个采集小时的唯一 put 原生案卷，现有 0 份"
    assert blocked["Deribit_BTC_20261122T080000Z"]["rv_24h_at_1500_close_pct"] == pytest.approx(149.59, abs=1e-2)
    assert blocked["Deribit_BTC_20261122T080000Z"]["archive_any_displayed"] is False

    # Verify 2026-11-21 lifecycle & cross-venue quote-flicker vs OKX-corroborated surge
    lc21 = study["lifecycle_1121"]
    assert lc21["execution_status"] == "UNEXECUTED_LOCK_BLOCKED"
    assert lc21["intraday_low_usd"] == pytest.approx(55339.78, abs=1e-2)
    assert lc21["official_delivery_usd"] == pytest.approx(58007.44, abs=1e-2)
    assert lc21["counterfactual_put_payout_btc"] == pytest.approx(0.017111, abs=1e-6)
    assert lc21["counterfactual_call_payout_btc"] == 0.0

    traj_by_iso = {t["asof_iso"]: t for t in lc21["trajectory"]}
    # 16:08:29Z and 16:19:06Z Deribit single-venue quote pulls -> QUOTE_FLICKER_SUSPECT
    assert traj_by_iso["2026-11-20T16:08:29Z"]["put_feedback_state"] == "QUOTE_FLICKER_SUSPECT"
    assert traj_by_iso["2026-11-20T16:19:06Z"]["put_feedback_state"] == "QUOTE_FLICKER_SUSPECT"
    # 02:04:27Z 5.21x spike corroborated by OKX -> AMPLIFYING_SURGE while Put already at/past 59k
    t_0204 = traj_by_iso["2026-11-21T02:04:27Z"]
    assert t_0204["put_gfri"] == pytest.approx(5.213, abs=1e-2)
    assert t_0204["put_feedback_state"] == "AMPLIFYING_SURGE"
    assert t_0204["put_distance_pct"] < 0.0

    # Verify 2026-11-23 -> 2026-11-24 killer contrast & strict active-vs-post-revoke separation
    rows = study["official"]["structure_rows"]
    put_1123 = next(r for r in rows if r["dossier"] == "Deribit_BTC_20261124T080000Z" and r["side"] == "put")
    assert put_1123["is_shadow_locked"] is True
    assert put_1123["distance_pct"] == pytest.approx(1.2656, abs=1e-3)
    assert put_1123["rank_widest_distance_of_16"] == 3
    assert put_1123["short_abs_delta"] == pytest.approx(0.29919, abs=1e-4)
    assert put_1123["rank_lowest_abs_delta_distinct_contract"] == 3
    assert put_1123["iv_minus_rv24_vol_pts"] == pytest.approx(-35.61, abs=1e-2)
    assert put_1123["buffer_rv24_sigma"] == pytest.approx(0.320, abs=1e-3)
    assert put_1123["buffer_rv4h_5m_sigma"] == pytest.approx(0.131, abs=1e-3)
    assert put_1123["fast_vol_alert"] is True
    assert put_1123["esf_candidate_status_cn"] == "待校准 / 候选极高脆弱"

    # Verify 2026-11-19 16:02Z Put 62000-P GFRI=3.577x ELEVATED_PRESSURE
    put_1119 = next(r for r in rows if r["dossier"] == "Deribit_BTC_20261120T080000Z" and r["side"] == "put")
    assert put_1119["buffer_rv24_sigma"] == pytest.approx(0.748, abs=1e-3)
    assert put_1119["gfri_confirmed"] == pytest.approx(3.577, abs=1e-3)
    assert put_1119["feedback_state"] == "ELEVATED_PRESSURE"
    assert put_1119["settlement_short_breached"] is True

    lc23 = study["lifecycle_1123"]
    act23 = lc23["active_shadow_hold_truncated"]
    post23 = lc23["post_revoke_counterfactual"]
    assert act23["t0_net_credit_btc"] == pytest.approx(0.0026375, abs=1e-7)
    assert act23["lock_gfri_confirmed"] == pytest.approx(2.045, abs=1e-3)
    assert act23["first_62000_breach_1m_iso"] == "2026-11-23T16:47:00Z"
    assert act23["minutes_from_lock_to_breach"] == pytest.approx(34.653, abs=1e-2)
    assert act23["ai_revoke_observed_ms"] == 1795452608125
    assert act23["ai_revoke_decided_iso"] == "2026-11-23T16:50:51.876Z"
    assert act23["shadow_termination_iso"] == "2026-11-23T16:50:59.948Z"
    assert act23["shadow_termination_action"] == "REVOKE_RECORDED_NO_QUOTE"
    assert act23["stale_revoke_short_buyback_total_btc"] == pytest.approx(0.0098, abs=1e-6)
    assert act23["active_mae_log_vs_deal_pct"] == pytest.approx(1.44, abs=1e-2)
    assert post23["post_revoke_min_usd"] == pytest.approx(59443.89, abs=1e-2)
    assert post23["official_delivery_usd"] == pytest.approx(60370.78, abs=1e-2)
    assert post23["counterfactual_payout_btc"] == pytest.approx(0.026987, abs=1e-6)
    assert post23["counterfactual_hold_to_expiry_net_btc"] == pytest.approx(-0.024349, abs=1e-6)

    # Verify 4 shadow-locked positions AUC = 1.0 under B_RV24 and ESF Scheme 1
    m_shadow = study["head_to_head_metrics"]["official_shadow_locked_4"]
    assert m_shadow["auc_settlement_breach"]["baseline_b_rv24"] == 1.0
    assert m_shadow["auc_settlement_breach"]["candidate_c_esf_scheme1"] == 1.0

    # Verify UX card layout and zero IP leakage
    report_txt = format_study_report(study)
    assert "ESF v0.1 牌局结构脆弱度" in report_txt
    assert "结构状态：待校准 / 候选极高脆弱" in report_txt
    assert "模型权限：RESEARCH_ONLY" in report_txt
    assert len(inputs["archive_candidates"]) == 33
    assert all(r["sample_identity"] == "UNEXECUTED_ARCHIVE_CANDIDATE" for r in study["unexecuted_archive_candidates"])


def test_no_raw_ip_secret_or_katex_corruption_in_research_package() -> None:
    import gzip

    pkg_dir = Path(esf_core.__file__).resolve().parent
    ipv4_re = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    secret_re = re.compile(
        r"(?:BEGIN [A-Z ]*PRIVATE KEY|api_key\s*=\s*['\"][^'\"]+['\"]|Authorization:\s*Bearer\s+\S+|ASTRA_AI_SHADOW_LOCK_TOKEN\s*=)",
        re.IGNORECASE,
    )

    scanned_count = 0
    for p in sorted(pkg_dir.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix in (".py", ".md", ".json", ".jsonl"):
            txt = p.read_text(encoding="utf-8")
        elif p.name.endswith(".json.gz"):
            txt = gzip.open(p, "rt", encoding="utf-8").read()
        else:
            continue
        scanned_count += 1
        assert ipv4_re.search(txt) is None, f"Unexpected IPv4 pattern found in {p.relative_to(pkg_dir)}"
        assert secret_re.search(txt) is None, f"Unexpected secret pattern found in {p.relative_to(pkg_dir)}"

        if p.suffix == ".md":
            for lineno, line in enumerate(txt.splitlines(), start=1):
                # Strip inline code spans before checking for unescaped ($M) or broken table pipes
                stripped = re.sub(r"`[^`]*`", "", line)
                assert "($M" not in stripped, f"Unescaped ($M KaTeX hazard in {p.name}:{lineno}"
                if line.lstrip().startswith("|"):
                    for code_span in re.findall(r"`([^`]*)`", line):
                        # Every '|' inside a GFM table inline code span must be escaped as '\|'
                        unescaped_pipes = re.findall(r"(?<!\\)\|", code_span)
                        assert not unescaped_pipes, f"Unescaped '|' inside table code span in {p.name}:{lineno}"

    assert scanned_count >= 14
