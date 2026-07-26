import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_fmz_157_contract", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual))


def assert_close(actual, expected, message, eps=1e-9):
    if actual is None or abs(actual - expected) > eps:
        raise AssertionError(
            "{}: expected {}, got {}".format(message, expected, actual))


def funding_flow(raw, norm=1.0, effect="extreme_overcrowded",
                 data_ready=True):
    flow = {
        "tmvf_funding_effect": effect,
        "tmvf_48h": {
            "data_ready": data_ready,
            "funding_state": "extreme_crowded_long",
            "funding": {
                "funding_norm": norm,
                "funding_count": 12,
                "data_ready": data_ready,
            },
        },
    }
    if raw is not None:
        flow["last_funding_rate"] = raw
    return flow


def assert_non_voting_funding(verdict, expected_code, message):
    semantics = verdict["canonical_funding_semantics"]
    assert_equal(semantics["semantic_code"], expected_code, message + " code")
    assert_equal(semantics["is_crowded"], False, message + " crowding")
    assert_equal(semantics["reflexivity_importance"], "NOISE",
                 message + " reflexivity")
    assert_equal(semantics["edb_participation"], "NON_VOTING",
                 message + " participation")
    assert_equal(semantics["edb_vote_allowed"], False,
                 message + " edb vote allowed")
    assert_equal(semantics["compat_backfill_applied"], False,
                 message + " producer native")
    assert_true("未超过" in semantics["canonical_text_cn"],
                message + " canonical text")
    assert_equal(verdict["verdict"], "FUNDING_NEUTRAL",
                 message + " verdict")


def test_version_and_funding_raw_semantics(mod, config):
    assert_equal(config["demo_version"], "1.5.7", "FMZ producer version")

    cases = (
        (0.000004, "TEMPERATE_LONG_FUNDING"),
        (-0.000004, "TEMPERATE_SHORT_FUNDING"),
        (0.0001, "TEMPERATE_LONG_FUNDING"),
        (-0.0001, "TEMPERATE_SHORT_FUNDING"),
    )
    for raw, code in cases:
        verdict = mod.evaluate_funding_verdict(
            funding_flow(raw, norm=1.0, effect="extreme_overcrowded"),
            config)
        assert_non_voting_funding(
            verdict, code, "raw {!r}".format(raw))
        vote = mod._funding_vote(verdict, config)
        assert_close(vote["vote"], 0.0, "small raw funding vote")
        assert_close(vote["weight"], 0.0, "small raw funding weight")

    missing = mod.evaluate_funding_verdict(
        funding_flow(None, norm=1.0, effect="extreme_overcrowded"),
        config)
    missing_sem = missing["canonical_funding_semantics"]
    assert_equal(missing["verdict"], "FUNDING_UNABLE_TO_JUDGE",
                 "missing raw verdict")
    assert_equal(missing_sem["semantic_code"], "UNABLE_TO_JUDGE",
                 "missing raw semantic")
    assert_equal(missing_sem["edb_vote_allowed"], False,
                 "missing raw must not vote from norm")
    assert_true("不得由 funding_norm/effect 反推" in missing_sem["canonical_text_cn"],
                "missing raw canonical text")

    hard = mod.evaluate_funding_verdict(
        funding_flow(0.00010001, norm=0.86, effect="extreme_overcrowded"),
        config)
    hard_sem = hard["canonical_funding_semantics"]
    assert_equal(hard_sem["is_crowded"], True,
                 "strict raw excess can be crowded")
    assert_equal(hard_sem["edb_vote_allowed"], True,
                 "strict raw excess can become candidate")
    assert_equal(hard["verdict"], "FUNDING_HARD_WARNING",
                 "strict raw plus strict normalized excess hard warning")
    hard_vote = mod._funding_vote(hard, config)
    assert_equal(
        mod._evidence_participation_status(hard_vote),
        "GATE_ONLY",
        "hard Funding warning is a gate, not a direction vote",
    )

    crowded = mod.evaluate_funding_verdict(
        funding_flow(0.00010001, norm=0.70, effect="overcrowded"),
        config)
    crowded_vote = mod._funding_vote(crowded, config)
    assert_true(crowded_vote["weight"] > 0.0,
                "crowded raw with normalized evidence may vote")
    assert_true(crowded_vote["vote"] < 0.0,
                "crowded positive funding is bearish reflexivity")


def test_funding_adjustment_and_market_state_use_raw_semantics(mod, config):
    small = mod.funding_adjustment(
        0.60, 1.0, config, funding_rate=0.000004)
    assert_close(small["adjustment"], 0.0,
                 "small raw adjustment must be zero")
    assert_equal(small["effect"], "neutral",
                 "small raw effect must stay neutral")
    assert_equal(
        small["canonical_funding_semantics"]["edb_participation"],
        "NON_VOTING",
        "small raw semantics are non-voting")

    missing = mod.funding_adjustment(
        0.60, 1.0, config, funding_rate=None)
    assert_equal(missing["effect"], "unavailable",
                 "missing raw cannot use extreme norm")
    assert_equal(
        missing["canonical_funding_semantics"]["semantic_code"],
        "UNABLE_TO_JUDGE",
        "missing raw adjustment semantic")

    small_sem = mod.build_funding_canonical_semantics(
        0.000004, 1.0, "extreme_overcrowded", None, config)
    small_results = {
        "48h": {
            "funding_effect": "extreme_overcrowded",
            "funding": {"canonical_funding_semantics": small_sem},
        },
    }
    assert_true(
        mod._tmvf_market_state(
            mod.DIRECTION_BULLISH, small_results, config)
        != mod.MARKET_FUNDING_CROWDED,
        "small raw funding must not set crowded market_state")

    crowded_sem = mod.build_funding_canonical_semantics(
        0.00010001, 0.70, "overcrowded", None, config)
    crowded_results = {
        "48h": {
            "funding_effect": "overcrowded",
            "funding": {"canonical_funding_semantics": crowded_sem},
        },
    }
    assert_equal(
        mod._tmvf_market_state(
            mod.DIRECTION_BULLISH, crowded_results, config),
        mod.MARKET_FUNDING_CROWDED,
        "strict raw excess can set crowded market_state")


def weak_cvd_history():
    return [0.05 for _ in range(24)]


def strong_cvd_history():
    return [0.01 * (idx + 1) for idx in range(24)]


def test_cvd_small_evidence_does_not_activate_from_price_only(mod, config):
    window = {
        "data_ready": True,
        "cvd_norm": 0.02,
        "cvd_sum": 13.4245,
        "price_return_pct": 0.90,
    }
    vote = mod._cvd_window_vote(window, weak_cvd_history(), "4h", config)
    assert_equal(vote["detail"]["verdict"], "BUY_CONFIRMS_UP",
                 "quadrant verdict preserved for audit")
    assert_close(vote["vote"], 0.0,
                 "small CVD cannot be amplified by price")
    assert_close(vote["weight"], 0.0,
                 "small CVD must not participate")
    assert_equal(vote["detail"]["cvd_sum"], 13.4245,
                 "raw CVD fact preserved")
    assert_equal(vote["detail"]["price_confirm_active"], True,
                 "price side is valid but not sufficient")
    assert_equal(vote["detail"]["cvd_active"], False,
                 "CVD side remains inactive")


def test_cvd_weak_edge_and_long_short_mirror(mod, config):
    bullish = {
        "data_ready": True,
        "cvd_norm": 0.23,
        "cvd_sum": 210.0,
        "price_return_pct": 0.45,
    }
    bearish = dict(bullish)
    bearish["cvd_norm"] = -0.23
    bearish["cvd_sum"] = -210.0
    bearish["price_return_pct"] = -0.45

    bull_vote = mod._cvd_window_vote(
        bullish, strong_cvd_history(), "4h", config)
    bear_vote = mod._cvd_window_vote(
        bearish, strong_cvd_history(), "4h", config)
    expected_mag = 0.45 / config["edb_price_confirm_full_pct"]
    assert_close(bull_vote["vote"], expected_mag,
                 "bullish CVD weak-edge vote")
    assert_close(bear_vote["vote"], -expected_mag,
                 "bearish CVD weak-edge vote")
    assert_close(bull_vote["weight"], bear_vote["weight"],
                 "long/short CVD mirror weight")
    assert_equal(bull_vote["detail"]["joint_active"], True,
                 "bullish joint active")
    assert_equal(bear_vote["detail"]["joint_active"], True,
                 "bearish joint active")

    slow = mod._cvd_window_vote(
        bearish, strong_cvd_history(), "12h", config)
    assert_true(slow["weight"] > 0.0 and slow["vote"] < 0.0,
                "strong 12h bearish CVD remains effective")


def test_edb_keeps_small_4h_cvd_out_but_keeps_strong_12h(mod, config):
    flow = {
        "direction": mod.DIRECTION_BEARISH,
        "tmv_blend": -0.60,
        "last_funding_rate": 0.000004,
        "tmvf_funding_effect": "extreme_overcrowded",
        "tmvf_48h": {
            "funding": {
                "funding_norm": 1.0,
                "funding_count": 12,
                "data_ready": True,
            },
        },
        "micro_flow": {
            "fast_4h": {
                "data_ready": True,
                "cvd_norm": 0.02,
                "cvd_sum": 13.4245,
                "price_return_pct": 0.90,
            },
            "slow_12h": {
                "data_ready": True,
                "cvd_norm": -0.23,
                "cvd_sum": -240.0,
                "price_return_pct": -0.45,
            },
        },
    }
    edb = mod.evaluate_edb(
        flow,
        {"data_status": "unavailable", "macro_regime": "UNAVAILABLE"},
        {"is_active": True, "state": "NR_REPAIR_CONFIRMED"},
        skew=None,
        gamma_regime={"veto": False, "confidence_multiplier": 1.0},
        cvd_history={"4h": weak_cvd_history(), "12h": strong_cvd_history()},
        prev_edb_score=None,
        config=config,
    )
    evidence = {item["key"]: item for item in edb["evidence"]}
    assert_true("CVD_4h" in evidence,
                "weak 4h CVD must remain visible in the audit ledger")
    assert_equal(evidence["CVD_4h"]["participation_status"], "EXCLUDED",
                 "weak 4h CVD must stay excluded from scoring")
    assert_close(evidence["CVD_4h"]["eff_weight"], 0.0,
                 "weak 4h CVD must have zero effective weight")
    assert_true("CVD_12h" in evidence,
                "strong 12h CVD remains in EDB evidence")
    assert_equal(evidence["CVD_12h"]["participation_status"], "ACTIVE",
                 "strong 12h CVD remains active")
    assert_true(evidence["CVD_12h"]["vote"] < 0.0,
                "strong 12h CVD keeps bearish sign")
    assert_true("FUNDING" in evidence,
                "micro raw funding must remain visible in the audit ledger")
    assert_equal(evidence["FUNDING"]["participation_status"], "NON_VOTING",
                 "micro raw funding remains non-voting despite extreme norm")
    assert_close(evidence["FUNDING"]["eff_weight"], 0.0,
                 "micro raw funding must not affect scoring")

    card = mod.build_signal_review_card(
        {"edb": edb, "flow": flow},
        {"current_price": 65609.02},
        {"is_active": True, "state": "NR_REPAIR_CONFIRMED",
         "event_context": {"episode_id": "fmz157-audit-ledger"}},
        config,
    )
    record = mod.build_audit_record(card, config)
    ledger = {item["key"]: item for item in record["reasoning"]["evidence"]}
    assert_equal(ledger["CVD_4h"]["participation_status"], "EXCLUDED",
                 "audit card keeps weak CVD visible and excluded")
    assert_equal(ledger["FUNDING"]["participation_status"], "NON_VOTING",
                 "audit card keeps mild funding visible and non-voting")
    assert_equal(ledger["CVD_12h"]["participation_status"], "ACTIVE",
                 "audit card keeps strong CVD active")
    assert_close(ledger["CVD_4h"]["weighted_contribution"], 0.0,
                 "audit weak CVD contribution remains zero")
    assert_close(ledger["FUNDING"]["weighted_contribution"], 0.0,
                 "audit mild funding contribution remains zero")
    assert_true("CVD_4h" not in record["conflict"]["dissent_keys"],
                "excluded CVD cannot inflate conflict")
    assert_true("FUNDING" not in record["conflict"]["dissent_keys"],
                "non-voting funding cannot inflate conflict")


def test_noise_rows_do_not_reduce_coverage(mod, config):
    active = {
        "key": "TMV",
        "vote": -1.0,
        "weight": 0.34,
        "eff_weight": 0.34,
        "participation_status": "ACTIVE",
    }
    baseline = mod._coverage([active], config)
    noise_rows = [
        {
            "key": "CVD_4h",
            "vote": 0.0,
            "weight": 0.0,
            "eff_weight": 0.0,
            "participation_status": "EXCLUDED",
            "exclusion_reason": "CVD_STRENGTH_NOT_ACTIVE",
        },
        {
            "key": "FUNDING",
            "vote": 0.0,
            "weight": 0.0,
            "eff_weight": 0.0,
            "participation_status": "NON_VOTING",
            "exclusion_reason": "FUNDING_RAW_SEMANTIC_NON_VOTING",
        },
    ]
    assert_close(
        mod._coverage([active] + noise_rows, config),
        baseline,
        "inactive CVD and non-voting Funding must not reduce coverage",
    )

    missing_cvd = dict(noise_rows[0])
    missing_cvd["exclusion_reason"] = "CVD_DATA_NOT_READY"
    assert_true(
        mod._coverage([active, missing_cvd], config) < baseline,
        "a real CVD data gap must still reduce coverage",
    )
    missing_funding = dict(noise_rows[1])
    missing_funding["exclusion_reason"] = "FUNDING_RAW_MISSING"
    assert_true(
        mod._coverage([active, missing_funding], config) < baseline,
        "missing raw Funding must still reduce coverage",
    )


def test_read_only_execution_fields_not_rewritten_by_funding_cvd(mod, config):
    cfg = dict(config)
    cfg["read_only_demo"] = True
    sem = mod.build_funding_canonical_semantics(
        0.000004, 1.0, "extreme_overcrowded", None, cfg)
    edb = {
        "lean": "BULLISH_WEAK",
        "side_hint": mod.SIDE_PUT_CREDIT_SPREAD,
        "support_label": "TRADE_SUPPORT_WEAK",
        "confidence": 60,
        "edb_score": 0.50,
        "edb_score_raw": 0.50,
        "agreement": 1.0,
        "coverage": 1.0,
        "conflict_level": "LOW",
        "next_action": "ALLOW_DOWNSTREAM_WITH_CAUTION",
        "precondition": {
            "nr_active": True,
            "nr_state": "NR_REPAIR_CONFIRMED",
        },
        "evidence": [],
        "confidence_decomposition": {
            "strength": 0.5,
            "score_full": cfg["edb_score_full"],
            "agreement_floor": cfg["edb_agreement_floor"],
            "coverage_floor": cfg["edb_coverage_floor"],
            "ggr_mult": 1.0,
            "confidence_final": 60,
        },
    }
    factor_snapshot = {
        "edb": edb,
        "flow": {
            "last_funding_rate": 0.000004,
            "tmvf_funding_effect": "extreme_overcrowded",
            "tmvf_funding_semantics": sem,
        },
    }
    nr = {
        "is_active": True,
        "state": "NR_REPAIR_CONFIRMED",
        "event_context": {"episode_id": "fmz157-readonly"},
        "anchor_context": {"anchor_score": 72.0, "normalized_deviation": 0.05},
    }
    card = mod.build_signal_review_card(
        factor_snapshot,
        {"current_price": 100000.0},
        nr,
        cfg,
    )
    record = mod.build_audit_record(card, cfg)
    assert_equal(record["decision"]["trade_allowed"], False,
                 "read-only trade_allowed remains false")
    assert_equal(record["decision_matrix"]["execution_allowed"], False,
                 "execution_allowed remains false")
    assert_equal(record["decision_matrix"]["model_trade_support"], True,
                 "model support can remain auditable without execution")


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    test_version_and_funding_raw_semantics(mod, config)
    test_funding_adjustment_and_market_state_use_raw_semantics(mod, config)
    test_cvd_small_evidence_does_not_activate_from_price_only(mod, config)
    test_cvd_weak_edge_and_long_short_mirror(mod, config)
    test_edb_keeps_small_4h_cvd_out_but_keeps_strong_12h(mod, config)
    test_noise_rows_do_not_reduce_coverage(mod, config)
    test_read_only_execution_fields_not_rewritten_by_funding_cvd(mod, config)
    print("fmz_157_producer_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_157_producer_contract: FAIL - " + str(exc))
        sys.exit(1)
