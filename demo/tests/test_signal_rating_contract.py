import copy
import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
BASELINE_SHA = "2c7216229a0f64c95cc37355e6151bf0cee01189"
BASELINE_SIGNAL_PATH = "demo/最新交付物/neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_baseline_module():
    source = subprocess.check_output(
        ["git", "show", BASELINE_SHA + ":" + BASELINE_SIGNAL_PATH],
        cwd=str(ROOT),
        encoding="utf-8",
    )
    source = source.lstrip("\ufeff")
    module = types.ModuleType("nrd_signal_baseline")
    module.__file__ = BASELINE_SHA + ":" + BASELINE_SIGNAL_PATH
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual)
        )


def ms_utc8(year=2026, month=9, day=7, hour=11, minute=30):
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return int(datetime.datetime(
        year, month, day, hour, minute, tzinfo=tz).timestamp() * 1000)


def get_path(root, path):
    current = root
    for part in str(path or "").split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except Exception:
                return None
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def assert_refs_resolve(record):
    rating = record.get("signal_rating") or {}
    refs = list(((rating.get("context") or {}).get("source_refs") or []))
    for claim in (rating.get("claims") or {}).values():
        for bucket in ("required_inputs", "support", "opposition", "unknowns"):
            for item in claim.get(bucket) or []:
                if item.get("source_ref"):
                    refs.append(item["source_ref"])
    for ref in refs:
        assert_true(get_path(record, ref) is not None,
                    "signal_rating source_ref should resolve: " + str(ref))


def clone_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def funding_semantics(raw_available=True):
    return {
        "schema_name": "FundingCanonicalSemantics",
        "schema_version": "nrd.signal.funding_semantics.v1.0.0",
        "raw_available": bool(raw_available),
        "raw_funding_rate": 0.00003 if raw_available else None,
        "raw_source": "last_funding_rate" if raw_available else None,
        "edb_vote_allowed": False,
        "edb_participation": "NON_VOTING",
        "crowding_state": "NOT_CROWDED",
        "semantic_code": "TEMPERATE_LONG_FUNDING",
        "reflexivity_state": "NOISE",
        "canonical_text_cn": "Funding 原始费率温和，作为非投票背景。",
        "compat_backfill_applied": False,
    }


def ev(key, vote, weight=0.30, participation="ACTIVE",
       detail=None, exclusion_reason=None):
    if participation != "ACTIVE":
        weight = 0.0
    return {
        "key": key,
        "vote": vote,
        "weight": weight,
        "eff_weight": weight,
        "info": 1.0 if participation == "ACTIVE" else 0.0,
        "participation_status": participation,
        "detail": detail or {},
        "exclusion_reason": exclusion_reason,
    }


def set_directional_evidence(card, tmv=1, cvd=1, macro=1, srd=1,
                             confidence=42):
    rows = []
    if tmv is None:
        rows.append(ev("TMV", 0.0, participation="EXCLUDED",
                       detail={"direction": "Unclear", "tmv_blend": 0.0,
                               "window_conflict": True,
                               "tmvf_24h_final": -0.16,
                               "tmvf_48h_final": 0.46}))
    else:
        rows.append(ev("TMV", float(tmv) * 0.70,
                       detail={"direction": "Bullish" if tmv > 0 else (
                           "Bearish" if tmv < 0 else "Unclear"),
                               "tmv_blend": float(tmv) * 0.30}))
    if cvd is None:
        rows.append(ev("CVD_4h", 0.0, participation="EXCLUDED",
                       detail={"verdict": "FLAT"},
                       exclusion_reason="CVD_STRENGTH_NOT_ACTIVE"))
    else:
        verdict = "BUY_CONFIRMS_UP" if cvd > 0 else (
            "SELL_CONFIRMS_DOWN" if cvd < 0 else "FLAT")
        rows.append(ev("CVD_4h", float(cvd) * 0.40,
                       detail={"verdict": verdict, "joint_active": cvd != 0}))
    rows.append(ev("MACRO", float(macro) * 0.50 if macro else 0.0))
    rows.append(ev("SRD", float(srd) * 0.20 if srd else 0.0))
    rows.append(ev("FUNDING", 0.0, participation="NON_VOTING",
                   detail={"canonical_funding_semantics":
                           funding_semantics(True),
                           "verdict": "FUNDING_NEUTRAL"},
                   exclusion_reason="FUNDING_RAW_SEMANTIC_NON_VOTING"))
    card["reasoning"].update({
        "edb_score": 0.25 if tmv and tmv > 0 else (-0.25 if tmv and tmv < 0 else 0.0),
        "edb_score_raw": 0.25 if tmv and tmv > 0 else (-0.25 if tmv and tmv < 0 else 0.0),
        "agreement": 0.80,
        "coverage": 1.0,
        "evidence": rows,
        "confidence_decomposition": {
            "strength": 0.50,
            "agr_factor": 0.80,
            "cov_factor": 1.0,
            "ggr_mult": 1.0,
            "conf_pre_veto": confidence,
            "confidence_final": confidence,
        },
    })
    card["conclusion"]["confidence"] = confidence
    card["conflict"] = {
        "ratio": 0.20,
        "level": "WATCH",
        "aligned_keys": [],
        "dissent": [],
        "explanation_cn": "固定夹具冲突说明。",
    }


def make_card(mod, episode="EP-RATING", confidence=42):
    cfg = dict(mod.CONFIG)
    fixed_ms = ms_utc8()
    card = mod.build_sample_review_card(cfg)
    card["card_id"] = "rating-" + episode
    card["episode_id"] = episode
    card["confirmed_time"] = fixed_ms
    card["window"].update({
        "nr_state": "NR_REPAIR_CONFIRMED",
        "is_active": True,
        "episode_direction": "UP",
        "peak_m_die": 0.88,
        "event_count_merged": 3,
        "anchor_score": 72.0,
        "anchor_nd": 0.18,
        "session_context": mod.classify_signal_session_context(fixed_ms, cfg),
    })
    cross = card["factor_cross_section"]
    cross["anchor"] = {
        "score": 72.0,
        "anchor_gravity_ref_score": 72.0,
        "anchor_gravity_ref_label": "Attached",
        "normalized_deviation": 0.18,
        "freshness": "FRESH",
        "gex_freshness": "FRESH",
        "gex_source_ts_ms": fixed_ms - 15_000,
        "ready": True,
        "source_ref": "INTERNAL_DIE_ANCHOR",
    }
    cross["neutral_repair"] = {
        "state": "NR_REPAIR_CONFIRMED",
        "is_active": True,
        "age_ms": 0,
        "event_context": {"episode_id": episode, "episode_direction": "UP"},
        "anchor_context": {"anchor_score": 72.0,
                           "normalized_deviation": 0.18},
    }
    cross["tmvf"].update({
        "direction": "Bullish",
        "tmv_blend": 0.35,
        "age_ms": 45_000,
        "observed_at": "2026-09-07T11:29:15+08:00",
    })
    cross["micro_flow"] = {
        "source_ref": "BINANCE_AGG_TRADES",
        "data_status": "OK",
        "age_ms": 0,
        "observed_at": "2026-09-07T11:30:00+08:00",
        "fast_4h": {
            "data_ready": True,
            "cvd_norm": 0.40,
            "cvd_sum": 1200.0,
            "price_return_pct": 0.40,
        },
        "slow_12h": {
            "data_ready": True,
            "cvd_norm": 0.20,
            "cvd_sum": 800.0,
            "price_return_pct": 0.20,
        },
    }
    cross["funding"].update({
        "last_rate": 0.00003,
        "last_funding_rate": 0.00003,
        "age_ms": 45_000,
        "observed_at": "2026-09-07T11:29:15+08:00",
        "canonical_funding_semantics": funding_semantics(True),
    })
    cross["macro_pressure"].update({
        "score": 0.20,
        "macro_score": 0.20,
        "regime": "Tailwind",
        "macro_regime": "Tailwind",
        "data_status": "OK",
        "age_ms": 600_000,
        "data_age_ms": 600_000,
        "last_refresh_ms": fixed_ms - 60_000,
        "refresh_sec": 3600,
        "observed_at": "2026-09-07T11:20:00+08:00",
    })
    cross["gamma_regime"].update({
        "regime": "POSITIVE_GAMMA_PINNING",
        "veto": False,
        "net_gamma_notional_usd": 12_000_000.0,
        "age_ms": 120_000,
        "observed_at": "2026-09-07T11:28:00+08:00",
    })
    cross["gex_info"] = {
        "data_status": "OK",
        "quality": "OK",
        "market_state": "positive_gamma",
        "net_gamma_notional_usd": 11_000_000.0,
        "age_ms": 900_000,
        "observed_at": "2026-09-07T11:15:00+08:00",
        "source_ref": "GEX_MONITOR_API",
    }
    cross["skew"].update({
        "vote": 0.20,
        "rr_z": 0.10,
        "data_status": "OK",
        "age_ms": 120_000,
        "observed_at": "2026-09-07T11:28:00+08:00",
    })
    set_directional_evidence(card, 1, 1, 1, 1, confidence)
    return card


def build_record(mod, card):
    return mod.build_audit_record(card, dict(mod.CONFIG))


def required_input(claim, group):
    for item in claim.get("required_inputs") or []:
        if item.get("source_group") == group:
            return item
    raise AssertionError("missing required input group " + group)


def test_native_rating_contract_and_integrity(mod):
    record = build_record(mod, make_card(mod, "EP-CONTRACT"))
    rating = record.get("signal_rating")
    assert_equal(rating.get("schema"), "signal_rating@1.0.0",
                 "signal rating schema")
    assert_equal(rating.get("rating_scope"), "side_environment_v1",
                 "signal rating scope")
    assert_equal(rating.get("candidate_quote_economics"), "not_evaluated",
                 "candidate economics boundary")
    assert_equal(sorted((rating.get("claims") or {}).keys()),
                 ["call_pressure", "put_pressure", "structure"],
                 "fixed claim names")
    for claim in (rating.get("claims") or {}).values():
        assert_true(set(claim.keys()) == {
            "status", "summary_cn", "required_inputs",
            "support", "opposition", "unknowns",
        }, "claim has frozen field set")
    assert_equal(rating["context"]["future_validity"], "unknown",
                 "rating must not invent signal lifetime")
    assert_true("ttl" not in json.dumps(rating, ensure_ascii=False).lower(),
                "rating should not expose an invented TTL")
    assert_refs_resolve(record)

    original_hash = record["integrity"]["record_hash"]
    tampered = clone_json(record)
    tampered["signal_rating"]["candidate_quote_economics"] = "evaluated"
    tampered.pop("integrity", None)
    assert_true(mod._audit_integrity(tampered)["record_hash"] != original_hash,
                "integrity hash should cover native signal_rating")


def test_legacy_machine_fields_match_baseline(mod):
    baseline = load_baseline_module()
    old_record = baseline.build_audit_record(
        make_card(baseline, "EP-GOLDEN"), dict(baseline.CONFIG))
    new_record = build_record(mod, make_card(mod, "EP-GOLDEN"))
    for key in (
            "quality", "market_context", "decision", "display_layers",
            "signal_window", "decision_matrix", "blocking", "reasoning",
            "conflict", "factor_cross_section", "delivery",
            "signal_durability", "comfort_window", "price_anchor_durability"):
        assert_equal(new_record.get(key), old_record.get(key),
                     "legacy field changed: " + key)
    assert_equal(old_record["identity"]["strategy_version"], "1.5.7",
                 "baseline version")
    assert_equal(new_record["identity"]["strategy_version"], "1.6.0",
                 "new producer version")
    assert_true("signal_rating" not in old_record,
                "baseline should not have native rating")


def test_same_confidence_different_evidence_changes_side_rating(mod):
    bull = make_card(mod, "EP-BULL", confidence=42)
    bear = make_card(mod, "EP-BEAR", confidence=42)
    set_directional_evidence(bull, 1, 1, 1, 1, confidence=42)
    set_directional_evidence(bear, -1, -1, -1, -1, confidence=42)
    bull_record = build_record(mod, bull)
    bear_record = build_record(mod, bear)
    assert_equal(bull_record["decision"]["confidence"], 42,
                 "bull confidence fixture")
    assert_equal(bear_record["decision"]["confidence"], 42,
                 "bear confidence fixture")
    assert_equal(bull_record["signal_rating"]["claims"]["put_pressure"]["status"],
                 "SUPPORTED", "bullish evidence should support put spread environment")
    assert_equal(bull_record["signal_rating"]["claims"]["call_pressure"]["status"],
                 "OPPOSED", "bullish evidence should oppose call spread environment")
    assert_equal(bear_record["signal_rating"]["claims"]["put_pressure"]["status"],
                 "OPPOSED", "bearish evidence should oppose put spread environment")
    assert_equal(bear_record["signal_rating"]["claims"]["call_pressure"]["status"],
                 "SUPPORTED", "bearish evidence should support call spread environment")


def test_neutral_label_is_not_mean_reversion_or_trend_acceleration(mod):
    card = make_card(mod, "EP-NEUTRAL", confidence=60)
    set_directional_evidence(card, None, None, 0, 0, confidence=60)
    card["factor_cross_section"]["tmvf"]["market_state"] = "Anchor Mean-Reversion"
    record = build_record(mod, card)
    rating = record["signal_rating"]
    assert_equal(rating["claims"]["put_pressure"]["status"], "INSUFFICIENT",
                 "neutral evidence should not support put pressure")
    assert_equal(rating["claims"]["call_pressure"]["status"], "INSUFFICIENT",
                 "neutral evidence should not support call pressure")
    assert_equal(rating["market_state"]["legacy_label"],
                 "Anchor Mean-Reversion",
                 "legacy neutral fallback label")
    assert_true("回归尚未证明" in rating["market_state"]["interpretation_cn"],
                "neutral fallback must not claim mean reversion proof")
    assert_true(rating["market_state"]["trend_acceleration_available"] is False,
                "reserved trend acceleration enum must stay unavailable")
    assert_true(any("window_conflict=True" in item["reason_cn"]
                    for item in rating["claims"]["put_pressure"]["unknowns"]),
                "TMVF excluded without reason should preserve window conflict")
    assert_true(any("24h=" in item["reason_cn"] and "48h=" in item["reason_cn"]
                    for item in rating["claims"]["put_pressure"]["unknowns"]),
                "TMVF excluded row should preserve both horizon finals")


def test_conflict_and_funding_non_voting_vs_missing(mod):
    card = make_card(mod, "EP-CONFLICT", confidence=50)
    set_directional_evidence(card, 1, None, -1, -1, confidence=50)
    record = build_record(mod, card)
    put_claim = record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(put_claim["status"], "CONFLICTED",
                 "opposing pressure groups should stay local conflict")
    assert_true(required_input(put_claim, "Funding")["usable"] is True,
                "raw Funding should be usable even when non-voting")
    assert_true(any("非投票" in item["reason_cn"]
                    for item in put_claim["unknowns"]),
                "non-voting Funding should be explained separately")

    missing = make_card(mod, "EP-FUNDING-MISSING", confidence=50)
    set_directional_evidence(missing, 1, None, -1, -1, confidence=50)
    funding = missing["factor_cross_section"]["funding"]
    funding["last_rate"] = None
    funding["last_funding_rate"] = None
    funding["canonical_funding_semantics"] = funding_semantics(False)
    missing_record = build_record(mod, missing)
    missing_claim = missing_record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(missing_claim["status"], "INSUFFICIENT",
                 "missing raw Funding should make pressure claim insufficient")
    assert_true(required_input(missing_claim, "Funding")["usable"] is False,
                "missing raw Funding should not be usable")


def test_source_health_rejects_stale_future_and_default_ok(mod):
    cfg = dict(mod.CONFIG)
    old = make_card(mod, "EP-OLD-TMV")
    old["factor_cross_section"]["tmvf"]["age_ms"] = 100 * 24 * 60 * 60 * 1000
    old_record = build_record(mod, old)
    old_put = old_record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(required_input(old_put, "TMVF")["status"], "STALE",
                 "100-day TMVF data should be stale despite OK status")
    assert_equal(old_put["status"], "INSUFFICIENT",
                 "stale required source should make claim insufficient")

    default_ok = make_card(mod, "EP-DEFAULT-OK")
    micro = default_ok["factor_cross_section"]["micro_flow"]
    for key in ("age_ms", "observed_at", "data_status"):
        micro.pop(key, None)
    default_record = build_record(mod, default_ok)
    default_put = default_record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(required_input(default_put, "CVD")["status"],
                 "UNKNOWN_FRESHNESS",
                 "default OK without timing should not prove CVD freshness")

    anchor_no_ts = make_card(mod, "EP-ANCHOR-NO-TS")
    anchor_no_ts["factor_cross_section"]["anchor"].pop("gex_source_ts_ms", None)
    no_ts_record = build_record(mod, anchor_no_ts)
    no_ts_structure = no_ts_record["signal_rating"]["claims"]["structure"]
    assert_equal(required_input(no_ts_structure, "Anchor")["status"],
                 "UNKNOWN_FRESHNESS",
                 "Anchor must use its own source timestamp")
    assert_true(any("不能借 neutral_repair.age_ms" in item["reason_cn"]
                    for item in no_ts_structure["unknowns"]),
                "Anchor should not borrow neutral_repair freshness")

    expired_anchor = make_card(mod, "EP-ANCHOR-EXPIRED")
    expired_anchor["factor_cross_section"]["anchor"]["gex_source_ts_ms"] = (
        ms_utc8() - int(cfg["gex_freshness_expired_ms"]) - 1)
    expired_record = build_record(mod, expired_anchor)
    expired_structure = expired_record["signal_rating"]["claims"]["structure"]
    assert_equal(required_input(expired_structure, "Anchor")["status"],
                 "EXPIRED",
                 "Anchor should use existing gex freshness expiry")

    future_source = make_card(mod, "EP-FUTURE")
    future_source["factor_cross_section"]["skew"]["age_ms"] = -1
    future_record = build_record(mod, future_source)
    future_claim = future_record["signal_rating"]["claims"]["call_pressure"]
    assert_equal(required_input(future_claim, "SRD")["status"],
                 "FUTURE_SOURCE_TIME",
                 "negative age should be rejected")

    bad_observed = make_card(mod, "EP-BAD-OBSERVED")
    skew = bad_observed["factor_cross_section"]["skew"]
    skew.pop("age_ms", None)
    skew["observed_at"] = "not-a-time"
    bad_observed_record = build_record(mod, bad_observed)
    bad_observed_claim = bad_observed_record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(required_input(bad_observed_claim, "SRD")["status"],
                 "UNKNOWN_FRESHNESS",
                 "unparseable observed_at without age should be rejected")

    stale_observed = make_card(mod, "EP-STALE-OBSERVED")
    stale_skew = stale_observed["factor_cross_section"]["skew"]
    stale_skew["age_ms"] = 0
    stale_skew["observed_at"] = "2026-09-07T10:00:00+08:00"
    stale_observed_record = build_record(mod, stale_observed)
    stale_observed_claim = stale_observed_record["signal_rating"]["claims"]["put_pressure"]
    assert_equal(required_input(stale_observed_claim, "SRD")["status"],
                 "STALE",
                 "stale observed_at should override a misleading age=0")


def test_cvd_bad_child_window_does_not_vote_for_group(mod):
    card = make_card(mod, "EP-CVD-CHILD")
    set_directional_evidence(card, None, 0, 0, 0, confidence=55)
    card["reasoning"]["evidence"] = [
        ev("CVD_4h", 0.70, detail={"verdict": "BUY_CONFIRMS_UP"}),
        ev("CVD_12h", -0.60, detail={"verdict": "SELL_CONFIRMS_DOWN"}),
        ev("TMV", 0.0, participation="EXCLUDED",
           detail={"direction": "Unclear"}),
        ev("MACRO", 0.0),
        ev("SRD", 0.0),
        ev("FUNDING", 0.0, participation="NON_VOTING",
           detail={"canonical_funding_semantics": funding_semantics(True)},
           exclusion_reason="FUNDING_RAW_SEMANTIC_NON_VOTING"),
    ]
    micro = card["factor_cross_section"]["micro_flow"]
    micro["fast_4h"]["data_ready"] = False
    micro["slow_12h"]["data_ready"] = True
    record = build_record(mod, card)
    put_claim = record["signal_rating"]["claims"]["put_pressure"]
    call_claim = record["signal_rating"]["claims"]["call_pressure"]
    assert_equal(put_claim["status"], "OPPOSED",
                 "bad bullish CVD child should not support put pressure")
    assert_equal(call_claim["status"], "SUPPORTED",
                 "usable bearish CVD child should still support call pressure")
    assert_true(any(item["source_ref"] == "factor_cross_section.micro_flow.fast_4h"
                    for item in put_claim["unknowns"]),
                "bad CVD child should leave a local unknown")


def test_ggr_uses_regime_veto_not_legacy_multiplier_or_stale_gex(mod):
    multiplier_zero = make_card(mod, "EP-GGR-MULT")
    gamma = multiplier_zero["factor_cross_section"]["gamma_regime"]
    gamma["regime"] = "TRANSITION"
    gamma["confidence_multiplier"] = 0.0
    gamma["veto"] = False
    record = build_record(mod, multiplier_zero)
    structure = record["signal_rating"]["claims"]["structure"]
    assert_true(not any(item["source_group"] == "GGR"
                        for item in structure["opposition"]),
                "GGR confidence_multiplier must not become rating opposition")

    stale_gex = make_card(mod, "EP-STALE-GEX")
    stale_gex["factor_cross_section"]["gex_info"].update({
        "market_state": "negative_gamma",
        "age_ms": int(dict(mod.CONFIG)["gex_info_cache_max_age_ms"]) + 1,
    })
    stale_record = build_record(mod, stale_gex)
    stale_structure = stale_record["signal_rating"]["claims"]["structure"]
    assert_true(not any(item["source_ref"] == "factor_cross_section.gex_info"
                        for item in stale_structure["opposition"]),
                "stale gex_info must not be revived as GGR opposition")



def test_fixed_round_uses_snapshot_collected_time_as_rating_asof(mod):
    fixed_ms = ms_utc8()
    card = make_card(mod, "EP-FIXED-ASOF")
    card["confirmed_time"] = fixed_ms
    card["analysis_round"] = {
        "round_type": "US_FIXED_ANALYSIS_ROUND",
        "scheduled_time_ms": fixed_ms,
        "scheduled_time_utc8": "2026-09-07T11:30:00+08:00",
        "snapshot_collected_time_utc8": "2026-09-07T11:30:10+08:00",
    }
    card["factor_cross_section"]["anchor"]["gex_source_ts_ms"] = fixed_ms + 5_000
    card["factor_cross_section"]["tmvf"].update({
        "age_ms": 5_000,
        "observed_at": "2026-09-07T11:30:05+08:00",
    })
    card["factor_cross_section"]["macro_pressure"].update({
        "last_refresh_ms": fixed_ms + 5_000,
        "observed_at": "2026-09-07T11:30:05+08:00",
    })
    record = build_record(mod, card)
    rating = record["signal_rating"]
    assert_equal(rating["as_of_ms"], fixed_ms + 10_000,
                 "fixed analysis round should rate at actual snapshot time")
    assert_equal(required_input(rating["claims"]["structure"], "Anchor")["status"],
                 "OK", "source after scheduled but before snapshot is not future")
    assert_equal(required_input(rating["claims"]["put_pressure"], "TMVF")["status"],
                 "OK", "TMVF observed after scheduled but before snapshot is usable")

    no_actual = make_card(mod, "EP-FIXED-NO-ACTUAL")
    no_actual["confirmed_time"] = fixed_ms
    no_actual["analysis_round"] = {
        "round_type": "US_FIXED_ANALYSIS_ROUND",
        "scheduled_time_ms": fixed_ms,
        "scheduled_time_utc8": "2026-09-07T11:30:00+08:00",
    }
    no_actual_record = build_record(mod, no_actual)
    no_actual_rating = no_actual_record["signal_rating"]
    assert_equal(no_actual_rating["as_of_ms"], None,
                 "fixed round without actual collection time has unknown rating time")
    assert_equal(required_input(no_actual_rating["claims"]["put_pressure"], "TMVF")["status"],
                 "UNKNOWN_FRESHNESS",
                 "scheduled time alone cannot prove source freshness")

def test_repeated_episode_uses_existing_tracker_and_nr_context(mod):
    cfg = dict(mod.CONFIG)
    fixed_ms = ms_utc8()
    tracker = mod.SignalEventTracker(cfg)
    signal = {
        "state": "NR_REPAIR_CONFIRMED",
        "is_active": True,
        "event_context": {
            "episode_id": "EP-DUP",
            "episode_direction": "UP",
            "peak_m_die": 0.91,
            "event_count_merged": 2,
        },
        "anchor_context": {"anchor_score": 72.0,
                           "normalized_deviation": 0.10},
    }
    factor_snapshot = {
        "edb": {
            "precondition": {"nr_active": True,
                             "nr_state": "NR_REPAIR_CONFIRMED"},
            "edb_score": 0.25,
            "edb_score_raw": 0.25,
            "agreement": 0.80,
            "coverage": 1.0,
            "confidence": 42,
            "calibration_state": "UNCALIBRATED",
            "lean": "NEUTRAL",
            "side_hint": "none",
            "support_label": "WAIT_CONFIRMATION",
            "next_action": "WAIT_FOR_EVIDENCE",
            "confidence_decomposition": {
                "strength": 0.50,
                "agr_factor": 0.80,
                "cov_factor": 1.0,
                "ggr_mult": 1.0,
                "conf_pre_veto": 42,
                "confidence_final": 42,
            },
            "evidence": [
                ev("TMV", 0.60),
                ev("MACRO", 0.30),
                ev("SRD", 0.10),
                ev("FUNDING", 0.0, participation="NON_VOTING",
                   detail={"canonical_funding_semantics":
                           funding_semantics(True)},
                   exclusion_reason="FUNDING_RAW_SEMANTIC_NON_VOTING"),
            ],
        },
        "flow": {
            "direction": "Bullish",
            "tmv_blend": 0.35,
            "window_conflict": False,
            "last_funding_rate": 0.00003,
            "tmvf_funding_semantics": funding_semantics(True),
            "micro_flow": {
                "fast_4h": {"data_ready": True, "cvd_norm": 0.30,
                            "cvd_sum": 500.0, "price_return_pct": 0.30},
            },
        },
            "macro_pressure": {"score": 0.2, "regime": "Tailwind",
                           "data_status": "OK", "age_ms": 600_000,
                           "data_age_ms": 600_000,
                           "last_refresh_ms": fixed_ms - 60_000,
                           "refresh_sec": 3600},
        "gamma_regime": {"regime": "POSITIVE_GAMMA_PINNING",
                         "veto": False, "age_ms": 120_000},
        "gex_info": {"data_status": "OK", "market_state": "positive_gamma",
                     "net_gamma_notional_usd": 10_000_000.0, "age_ms": 900_000},
        "skew": {"vote": 0.1, "rr_z": 0.1, "data_status": "OK",
                 "age_ms": 120_000},
        "m_die": {"m_die": 0.91, "direction": "UP"},
        "anchor": {
            "score": 72.0,
            "anchor_gravity_ref_score": 72.0,
            "anchor_gravity_ref_label": "Attached",
            "normalized_deviation": 0.10,
            "freshness": "FRESH",
            "gex_freshness": "FRESH",
            "gex_source_ts_ms": fixed_ms - 15_000,
            "ready": True,
        },
    }
    runtime_facts = {
        "current_price": 80_000.0,
        "tmvf_data_age_ms": 45_000,
        "option_greeks_age_ms": 120_000,
        "last_cycle_trade_count": 20,
        "gex_fetch_age_ms": 900_000,
    }
    assert_true(tracker.maybe_record(signal, factor_snapshot, runtime_facts),
                "first episode should be recorded")
    assert_true(not tracker.maybe_record(signal, factor_snapshot, runtime_facts),
                "duplicate episode should not create a second card")
    assert_equal(len(tracker.events), 1,
                 "SignalEventTracker should remain the only episode state")
    record = build_record(mod, tracker.events[0])
    assert_equal(record["signal_rating"]["context"]["episode_id"], "EP-DUP",
                 "rating should reuse existing episode id")
    assert_equal(record["signal_rating"]["context"]["nr_state"],
                 "NR_REPAIR_CONFIRMED",
                 "rating should reuse existing NR state")
    assert_equal(record["signal_rating"]["context"]["future_validity"],
                 "unknown",
                 "rating should not create a second lifecycle or lifetime")


def main():
    mod = load_signal_module()
    assert_equal(dict(mod.CONFIG)["demo_version"], "1.6.0",
                 "FMZ signal deliverable version")
    test_native_rating_contract_and_integrity(mod)
    test_legacy_machine_fields_match_baseline(mod)
    test_same_confidence_different_evidence_changes_side_rating(mod)
    test_neutral_label_is_not_mean_reversion_or_trend_acceleration(mod)
    test_conflict_and_funding_non_voting_vs_missing(mod)
    test_source_health_rejects_stale_future_and_default_ok(mod)
    test_cvd_bad_child_window_does_not_vote_for_group(mod)
    test_ggr_uses_regime_veto_not_legacy_multiplier_or_stale_gex(mod)
    test_fixed_round_uses_snapshot_collected_time_as_rating_asof(mod)
    test_repeated_episode_uses_existing_tracker_and_nr_context(mod)
    print("signal_rating_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_rating_contract: FAIL - " + str(exc))
        sys.exit(1)
