"""Unit and empirical verification tests for research.esf_v01.convergence_study."""
from __future__ import annotations

import pytest

from research.esf_v01 import convergence_study as cs


@pytest.fixture(scope="module")
def study_results():
    return cs.run_convergence_study()


def test_verdict_enums_and_safety_flags(study_results) -> None:
    assert cs.CONVERGENCE_VERDICT == "ESF_NO_USEFUL_GFRI_INCREMENT"
    assert cs.SUPPLEMENT_P2_VERDICT == "EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED"
    assert cs.STANDALONE_ESF_STATUS == "ESF_REDUNDANT"
    assert cs.SUPPLEMENT_P2_VERDICT in cs.SUPPLEMENT_P2_ALLOWED_VERDICTS
    assert cs.STANDALONE_ESF_STATUS in cs.SUPPLEMENT_P2_ALLOWED_VERDICTS
    assert cs.RECOMMENDED_PATHS == (
        "PATH_2_ENHANCE_EXISTING_WORKBENCH",
        "PATH_3_KEEP_GFRI_SHADOW_TERMINATE_ESF",
    )
    assert cs.DECISION_AUTHORITY == "RESEARCH_ONLY"
    assert cs.LIVE_VETO_ENABLED is False
    assert cs.PLAYER_INTEGRATION_ENABLED is False

    assert study_results["convergence_verdict"] == "ESF_NO_USEFUL_GFRI_INCREMENT"
    assert study_results["supplement_p2_verdict"] == "EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED"
    assert study_results["standalone_esf_status"] == "ESF_REDUNDANT"
    assert study_results["live_veto_enabled"] is False
    assert study_results["player_integration_enabled"] is False


def test_zero_lookahead_prior_4h_depth_and_align_gfri() -> None:
    t0 = 1_795_200_000_000
    base_records = [
        {
            "freeze_at_ms": t0 - 3_600_000,
            "freeze_iso": "t_minus_60m",
            "put": {"status": "VALID", "gfri_confirmed": 2.4, "liquidity_depth_usd": 40_000_000.0},
            "call": {"status": "VALID", "gfri_confirmed": 2.3, "liquidity_depth_usd": 42_000_000.0},
            "okx_ref": {"down_usd": 80_000_000.0, "up_usd": 82_000_000.0},
            "near_ref": {"state_put": "READY", "delta_ratio_put": 1.0},
        },
        {
            "freeze_at_ms": t0 - 1_800_000,
            "freeze_iso": "t_minus_30m",
            "put": {"status": "VALID", "gfri_confirmed": 2.5, "liquidity_depth_usd": 36_000_000.0},
            "call": {"status": "VALID", "gfri_confirmed": 2.4, "liquidity_depth_usd": 38_000_000.0},
            "okx_ref": {"down_usd": 76_000_000.0, "up_usd": 78_000_000.0},
            "near_ref": {"state_put": "READY", "delta_ratio_put": 1.02},
        },
        {
            "freeze_at_ms": t0 - 120_000,
            "freeze_iso": "t_current",
            "put": {"status": "VALID", "gfri_confirmed": 3.0, "liquidity_depth_usd": 30_400_000.0},
            "call": {"status": "VALID", "gfri_confirmed": 2.9, "liquidity_depth_usd": 31_000_000.0},
            "okx_ref": {"down_usd": 70_200_000.0, "up_usd": 72_000_000.0},
            "near_ref": {"state_put": "READY", "delta_ratio_put": 1.22},
        },
    ]
    clean_align = cs.align_gfri(base_records, t0, "put")
    clean_4h = cs.compute_prior_4h_depth_change_pct(base_records, t0, side="put")
    assert clean_align is not None
    assert clean_align["freeze_at_ms"] == t0 - 120_000
    assert clean_4h["prior_count"] == 2
    # Median of strictly prior (40M, 36M) = 38M; 30.4M / 38M - 1 = -20.0%
    assert clean_4h["deribit_prior_4h_median_usd"] == pytest.approx(38_000_000.0)
    assert clean_4h["deribit_chg_4h_pct"] == pytest.approx(-20.0, abs=1e-9)

    # Inject catastrophic future book collapse at t0 + 1ms -> t0 metrics MUST NOT change
    with_future = list(base_records) + [
        {
            "freeze_at_ms": t0 + 1,
            "freeze_iso": "t_future_plus_1ms",
            "put": {"status": "VALID", "gfri_confirmed": 999.0, "liquidity_depth_usd": 100.0},
            "call": {"status": "VALID", "gfri_confirmed": 999.0, "liquidity_depth_usd": 100.0},
            "okx_ref": {"down_usd": 100.0, "up_usd": 100.0},
            "near_ref": {"state_put": "READY", "delta_ratio_put": 400.0},
        }
    ]
    fut_align = cs.align_gfri(with_future, t0, "put")
    fut_4h = cs.compute_prior_4h_depth_change_pct(with_future, t0, side="put")
    assert fut_align is not None
    assert fut_align["freeze_at_ms"] == clean_align["freeze_at_ms"]
    assert fut_4h["deribit_chg_4h_pct"] == pytest.approx(clean_4h["deribit_chg_4h_pct"], rel=0.0, abs=1e-12)
    assert fut_4h["okx_chg_4h_pct"] == pytest.approx(clean_4h["okx_chg_4h_pct"], rel=0.0, abs=1e-12)


def test_g1_esf_scheme1_zero_rank_upgrades_and_b_rv24_equivalence(study_results) -> None:
    g1 = study_results["g1_prior_claims"]
    assert g1["official_structures_count"] == 16
    assert g1["live_gfri_structures_count"] == 6
    assert g1["esf_scheme1_rank_upgrades_of_16"] == 0
    assert g1["esf_scheme1_rank_upgrades_live_gfri_of_6"] == 0
    assert g1["esf_scheme1_identical_to_b_rv24"] is True
    assert g1["n4_shadow_locked_descriptive_only"] is True
    assert g1["statistical_claim_allowed"] is False

    eq = g1["round1_metric_equivalence"]
    assert eq["n4_shadow_locked_auc_b_rv24"] == pytest.approx(1.0)
    assert eq["n4_shadow_locked_auc_esf_scheme1"] == pytest.approx(1.0)
    assert eq["n4_shadow_locked_auc_esf_scheme2"] == pytest.approx(2.0 / 3.0, abs=1e-4)
    assert eq["n16_settlement_auc_b_rv24"] == pytest.approx(0.5636, abs=1e-3)
    assert eq["n16_settlement_auc_esf_scheme1"] == pytest.approx(eq["n16_settlement_auc_b_rv24"], abs=1e-12)
    assert eq["n16_settlement_auc_esf_scheme2"] == pytest.approx(0.5455, abs=1e-3)
    assert eq["n16_intraday_auc_esf_scheme1"] == pytest.approx(eq["n16_intraday_auc_b_rv24"], abs=1e-12)
    assert eq["n16_payout_spearman_esf_scheme1"] == pytest.approx(eq["n16_payout_spearman_b_rv24"], abs=1e-12)

    inv = g1["information_inventory"]
    assert set(inv) == {"EXISTING_INFORMATION", "CANDIDATE_NEW_INFORMATION", "UNPROVEN"}
    assert all(len(inv[k]) >= 2 for k in inv)


def test_p0_dual_timestamp_deal_and_lock_reconstructions(study_results) -> None:
    p0_rows = study_results["g3_g6_official_abcd"]["p0_deal_vs_lock_reconstructions"]
    by_dossier = {r["dossier"]: r for r in p0_rows if r["dossier"].startswith("Deribit_BTC_2026112")}

    c1120 = by_dossier["Deribit_BTC_20261120T080000Z"]
    assert c1120["side"] == "call"
    assert c1120["deal"]["d_gfri_confirmed"] == pytest.approx(3.4447, abs=1e-3)
    assert c1120["lock_t0"] is not None
    assert c1120["lock_t0"]["d_gfri_confirmed"] == pytest.approx(3.4447, abs=1e-3)

    c1121 = by_dossier["Deribit_BTC_20261121T080000Z"]
    assert c1121["execution_state"] == "UNEXECUTED_LOCK_BLOCKED"
    assert c1121["lock_t0"] is None
    assert c1121["deal"]["d_gfri_confirmed"] == pytest.approx(3.090, abs=1e-2)

    p1124 = by_dossier["Deribit_BTC_20261124T080000Z"]
    assert p1124["side"] == "put"
    assert p1124["deal"]["d_gfri_confirmed"] == pytest.approx(2.2354, abs=1e-3)
    assert p1124["deal"]["d_gate_2_5_veto"] is False
    assert p1124["lock_t0"] is not None
    assert p1124["lock_t0"]["d_gfri_confirmed"] == pytest.approx(2.0453, abs=1e-3)
    assert p1124["lock_t0"]["d_gate_2_5_veto"] is False


def test_a1_existing_rv24_exposes_1123_dual_fragility_without_gfri(study_results) -> None:
    rows = study_results["g3_g6_official_abcd"]["structures"]
    put_1123 = next(r for r in rows if r["dossier"] == "Deribit_BTC_20261124T080000Z" and r["side"] == "put")
    call_1123 = next(r for r in rows if r["dossier"] == "Deribit_BTC_20261124T080000Z" and r["side"] == "call")

    assert put_1123["a1_atm_iv_pct"] == pytest.approx(51.77, abs=1e-2)
    assert put_1123["a1_rv_24h_pct"] == pytest.approx(92.59, abs=1e-2)
    assert put_1123["a1_iv_minus_rv24_atm_pts"] == pytest.approx(-40.82, abs=1e-2)
    assert put_1123["a0_shown_scale_mult"] == pytest.approx(0.55, abs=1e-2)
    assert put_1123["a1_scale_eff_mult"] == pytest.approx(0.3053, abs=1e-3)
    assert put_1123["a1_buffer_rv24_sigma"] == pytest.approx(0.320, abs=1e-3)
    assert put_1123["a1_buffer_rv4h_5m_sigma"] == pytest.approx(0.131, abs=1e-3)

    assert call_1123["a0_shown_scale_mult"] == pytest.approx(0.17, abs=1e-2)
    assert call_1123["a1_scale_eff_mult"] == pytest.approx(0.0970, abs=1e-3)
    assert call_1123["a1_buffer_rv24_sigma"] == pytest.approx(0.090, abs=1e-3)

    # C(|HF|) was at its lowest level of all 8 hands on 11-23 Put ($69.74M L5 / $73.51M native)
    put_rows = [r for r in rows if r["side"] == "put"]
    assert put_1123["c_l5_hf_abs_musd"] == pytest.approx(69.736, abs=1e-2)
    assert put_1123["c_gfri_native_hf_abs_musd"] == pytest.approx(73.507, abs=1e-2)
    assert put_1123["c_l5_hf_abs_musd"] == min(r["c_l5_hf_abs_musd"] for r in put_rows)


def test_g6_marginal_gate_accounting_zero_credit_for_a0_rejects(study_results) -> None:
    gates = study_results["g3_g6_official_abcd"]["marginal_gate_accounting"]
    for g_name in ("D_GATE_2_5", "D_GATE_3_0_XVENUE", "D_GATE_3_5_OR_DR125"):
        g = gates[g_name]
        assert g["marginal_credit_for_already_a0_rejected_puts_btc"] == 0.0
        assert g["true_positive_marginal_blocks_over_a0"] == 0
        assert g["marginal_saved_counterfactual_payout_btc"] == 0.0
        assert g["false_negative_missed_accepted_breaches"] == 1
        assert g["false_negative_structures"] == ["Deribit_BTC_20261124T080000Z:put"]

    assert gates["D_GATE_2_5"]["false_positive_blocks_on_winning_a0_accepts"] == 2
    assert gates["D_GATE_3_0_XVENUE"]["false_positive_blocks_on_winning_a0_accepts"] == 2
    assert gates["D_GATE_2_5"]["forgone_visible_credit_on_locked_winners_btc"] == pytest.approx(0.0045)
    assert gates["D_GATE_2_5"]["forgone_counterfactual_net_credit_on_lock_blocked_winners_btc"] == pytest.approx(
        0.0069
    )


def test_active_hold_strict_truncation_1123_put_and_1119_call(study_results) -> None:
    audits = study_results["g3_g6_official_abcd"]["active_hold_gfri_audits"]
    c19 = audits["Deribit_BTC_20261120T080000Z_call_63500"]
    assert c19["active_hold_numerical_cycles"] == 90
    assert c19["shadow_pause_cycles"] == 90
    assert c19["call_gfri_ge_2_5_cycles"] == 90
    assert c19["call_gfri_ge_3_5_cycles"] == 44
    assert c19["call_gfri_min"] == pytest.approx(3.100, abs=1e-3)
    assert c19["call_gfri_p50"] == pytest.approx(3.499, abs=1e-3)
    assert c19["call_gfri_max"] == pytest.approx(6.186, abs=1e-3)
    assert c19["locked_settlement_entry_after_fee_btc"] is None
    assert c19["settlement_payout_btc"] == 0.0

    p23 = audits["Deribit_BTC_20261124T080000Z_put_62000"]
    assert p23["shadow_termination_ms"] == 1795452659948
    assert p23["shadow_termination_iso"] == "2026-11-23T16:50:59.948Z"
    assert p23["active_hold_numerical_cycles"] == 3
    assert p23["shadow_pause_cycles"] == 0
    assert p23["active_hold_shadow_decisions"] == ["NO_VETO", "NO_VETO", "NO_VETO"]
    assert p23["active_hold_put_gfri_values"] == pytest.approx([2.00408, 2.07387, 2.64272], abs=1e-4)
    assert p23["last_cycle_iso"] == "2026-11-23T16:41:45Z"
    assert p23["last_cycle_ob_ref_price_usd"] == pytest.approx(62071.75)
    assert p23["buffer_consumed_at_last_cycle_pct"] == pytest.approx(90.86, abs=0.05)
    assert p23["termination_action"] == "REVOKE_RECORDED_NO_QUOTE"
    assert p23["realized_exit_pnl_btc"] is None
    assert p23["post_revoke_excluded_from_active_hold"] is True

    # Verify post-revoke 22:01Z 4.19x spike & 59,443.89 trough are strictly isolated
    clk = study_results["g4_g5_buffer_and_strata"]["clock_separation_1123"]
    assert clk["ACTIVE_HOLD_TRUNCATED"]["end_ms"] == 1795452659948
    assert clk["ACTIVE_HOLD_TRUNCATED"]["max_put_gfri"] < 2.70
    assert clk["ACTIVE_HOLD_TRUNCATED"]["min_spot_1m_usd"] == pytest.approx(61892.19, abs=1e-2)
    assert clk["POST_REVOKE_COUNTERFACTUAL"]["spike_2201_put_gfri"] == pytest.approx(4.192, abs=1e-2)
    assert clk["POST_REVOKE_COUNTERFACTUAL"]["post_revoke_trough_1m_usd"] == pytest.approx(59443.89, abs=1e-2)


def test_p1_post_entry_non_duplicative_hold_criteria(study_results) -> None:
    p1 = study_results["g3_g6_official_abcd"]["p1_post_entry_audit"]
    assert p1["criterion_1_actionable_warning_before_workbench_triggers"] is False
    assert p1["criterion_2_new_executable_basis_after_triggers_given_quote_limits"] is False
    assert p1["criterion_3_cuts_severe_loss_without_redundant_false_alarms"] is False
    assert p1["post_entry_esf_redundant"] is True

    ev = p1["evidence_1123_put_62000"]
    assert "A:CONDITION_TRIGGERED" in ev["existing_workbench_dispatched_signatures"]
    assert "B:MARGIN_DETERIORATION" in ev["existing_workbench_dispatched_signatures"]
    assert ev["existing_workbench_review"]["verdict"] == "REVOKE"
    assert ev["last_pre_breach_safety_distance_pct"] == pytest.approx(0.1156, abs=1e-3)
    assert ev["realized_exit_pnl_invented"] is False


def test_non_overlapping_2h_market_windows_and_g5_strata(study_results) -> None:
    g4 = study_results["g4_g5_buffer_and_strata"]
    mc = g4["market_counts"]
    assert mc["n_all_120m_complete"] == 730
    assert mc["n_2h_non_overlapping"] == 62
    assert mc["n_ready_2h_prior_history"] == 61
    assert mc["min_2h_stride_ms"] >= 7_200_000

    rows_2h = g4["non_overlapping_2h_rows"]
    for idx in range(1, len(rows_2h)):
        assert rows_2h[idx]["freeze_at_ms"] - rows_2h[idx - 1]["freeze_at_ms"] >= 7_200_000

    # Verify V-bottom mean-reversion contrast in HIGH_RV_GE_70 (G3 < G0 subsequent 120m Put MAE)
    hi_all = g4["strata_all_overlapping"]["HIGH_RV_GE_70"]
    hi_2h = g4["strata_2h_non_overlapping"]["HIGH_RV_GE_70"]
    assert hi_all["G3_HIGH_GFRI_CROSS_VENUE_WITHDRAWAL"]["put_mae_120m_mean_pct"] == pytest.approx(0.463, abs=1e-2)
    assert hi_all["G0_NORMAL_GFRI_STABLE_BOOK"]["put_mae_120m_mean_pct"] == pytest.approx(0.879, abs=1e-2)
    assert hi_2h["G3_HIGH_GFRI_CROSS_VENUE_WITHDRAWAL"]["put_mae_120m_mean_pct"] == pytest.approx(0.363, abs=1e-2)
    assert hi_2h["G0_NORMAL_GFRI_STABLE_BOOK"]["put_mae_120m_mean_pct"] == pytest.approx(0.951, abs=1e-2)


def test_archive_4h_panel_expiry_cluster_disclosure_and_b_beats_cd(study_results) -> None:
    arch = study_results["g4_g5_buffer_and_strata"]["archive_4h_panel"]
    assert arch["sample_caveat"] == "EXPIRY_CLUSTERED_ARCHIVE_CANDIDATES_NOT_INDEPENDENT_EPISODES"
    assert arch["total_candidates"] == 51
    assert arch["ready_candidates"] == 50
    assert arch["distinct_expiries"] == 5

    thin = arch["thin_critical_b_lt_0_60_metrics"]
    assert thin["count"] == 30
    # Within THIN_CRITICAL (n=30), pure liquidity B(1/DeribitDepth) beats C(|HF|) and D(GFRI)
    auc_b = thin["auc_intraday_short_breach"]["B_inv_deribit_depth"]
    auc_c = thin["auc_intraday_short_breach"]["C_hf_abs_usd"]
    auc_d = thin["auc_intraday_short_breach"]["D_gfri"]
    assert auc_b > auc_d > auc_c
    assert thin["spearman_vs_intrusion_capped_frac"]["B_inv_deribit_depth"] > 0.15
    assert thin["spearman_vs_intrusion_capped_frac"]["C_hf_abs_usd"] < -0.20
    assert thin["spearman_vs_intrusion_capped_frac"]["B_inv_deribit_depth"] > thin[
        "spearman_vs_intrusion_capped_frac"
    ]["D_gfri"]


def test_20261121_0204z_pure_liquidity_attribution_and_5_counterexamples(study_results) -> None:
    g4 = study_results["g4_g5_buffer_and_strata"]
    ev = g4["event_20261121_0204z_attribution"]
    assert ev["onset_iso"] == "2026-11-21T02:04:27Z"
    assert abs(ev["hf_step_chg_pct"]) < 0.5
    assert ev["deribit_depth_step_chg_pct"] < -30.0
    assert ev["okx_depth_step_chg_pct"] < -5.0
    assert ev["b_depth_log_jump_share"] == pytest.approx(0.9992, abs=1e-3)

    ce = g4["mandatory_counterexamples"]
    assert len(ce) == 5
    assert [c["archetype_id"] for c in ce] == [1, 2, 3, 4, 5]


def test_data_blocked_dossiers_preserved_without_invented_trades(study_results) -> None:
    blocked = study_results["g3_g6_official_abcd"]["data_blocked_hands"]
    assert len(blocked) == 2
    assert {b["dossier"] for b in blocked} == {
        "Deribit_BTC_20261122T080000Z",
        "Deribit_BTC_20261123T080000Z",
    }


def test_classify_g5_regime_synthetic_boundaries() -> None:
    from research.esf_v01.convergence_study import classify_g5_regime

    assert classify_g5_regime(
        gfri_confirmed=None, delta_ratio=1.5, deribit_chg_4h_pct=-30.0, okx_chg_4h_pct=-20.0
    ) == "G_UNAVAILABLE"
    assert classify_g5_regime(
        gfri_confirmed=2.99, delta_ratio=1.5, deribit_chg_4h_pct=-30.0, okx_chg_4h_pct=-20.0
    ) == "G0_NORMAL_GFRI_STABLE_BOOK"
    assert classify_g5_regime(
        gfri_confirmed=3.55, delta_ratio=1.10, deribit_chg_4h_pct=-5.0, okx_chg_4h_pct=2.0
    ) == "G1_CHRONIC_HIGH_GFRI_STABLE_BOOK"
    assert classify_g5_regime(
        gfri_confirmed=3.05, delta_ratio=1.25, deribit_chg_4h_pct=-15.0, okx_chg_4h_pct=-9.9
    ) == "G2_HIGH_GFRI_SINGLE_VENUE_DERIBIT_THINNING"
    assert classify_g5_regime(
        gfri_confirmed=3.05, delta_ratio=1.10, deribit_chg_4h_pct=-20.0, okx_chg_4h_pct=-10.0
    ) == "G3_HIGH_GFRI_CROSS_VENUE_WITHDRAWAL"
