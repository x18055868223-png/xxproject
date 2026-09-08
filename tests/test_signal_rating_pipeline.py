import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MATERIALIZER_TOOL = ROOT / "tools" / "materialize_signal_cards.py"
LLM_TOOL = ROOT / "tools" / "signal_llm_review.py"
SELF_CHECK_TOOL = ROOT / "tools" / "server_self_check_signal_stack.sh"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def signal_rating(as_of_ms=1788753590501):
    return {
        "schema": "signal_rating@1.0.0",
        "rating_scope": "side_environment_v1",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": as_of_ms,
        "claims": {
            "structure": {
                "status": "SUPPORTED",
                "summary_cn": "Gamma 与 Anchor 结构可用，但不代表边界不可突破。",
                "required_inputs": ["factor_cross_section.anchor", "factor_cross_section.gex_info"],
                "support": [{
                    "source_ref": "factor_cross_section.gex_info",
                    "source_group": "GEX",
                    "basis_cn": "GEX 来源可用。",
                }],
                "opposition": [],
                "unknowns": [],
            },
            "put_pressure": {
                "status": "CONFLICTED",
                "summary_cn": "下方侵入压力有支持也有反对，需要人工复核。",
                "required_inputs": ["factor_cross_section.tmvf", "factor_cross_section.micro_flow"],
                "support": [{
                    "source_ref": "factor_cross_section.micro_flow",
                    "source_group": "FLOW",
                    "basis_cn": "短窗主动流偏弱。",
                }],
                "opposition": [{
                    "source_ref": "factor_cross_section.macro_pressure",
                    "source_group": "MACRO",
                    "basis_cn": "宏观背景未形成硬阻断。",
                }],
                "unknowns": [],
            },
            "call_pressure": {
                "status": "OPPOSED",
                "summary_cn": "上方侵入压力缺少足够支持。",
                "required_inputs": ["factor_cross_section.tmvf"],
                "support": [],
                "opposition": [{
                    "source_ref": "factor_cross_section.tmvf",
                    "source_group": "TMV",
                    "basis_cn": "主干方向没有向上加速。",
                }],
                "unknowns": [],
            },
        },
        "market_state": {
            "legacy_label": "Anchor Mean-Reversion",
            "interpretation_cn": "TMVF 方向中性，回归尚未证明。",
            "mean_reversion_validation": "not_established",
            "trend_acceleration_available": False,
        },
        "context": {
            "episode_id": "nr-demo",
            "nr_state": "NR_REPAIR_CONFIRMED",
            "nr_active": True,
            "analysis_round": None,
            "future_validity": "unknown",
            "source_refs": ["factor_cross_section.gex_info"],
        },
        "model_constraints": {
            "support_label": "WAIT_CONFIRMATION",
            "side_hint": "none",
            "has_block": True,
            "hard_veto": None,
            "execution_allowed": False,
        },
        "next_observations_cn": ["观察冲突是否继续收敛。"],
    }


def card(card_id, ts_ms, *, include_rating=False, invalid_rating=False):
    item = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-4:],
            "symbol": "BTC",
            "confirmed_time_ms": ts_ms,
            "confirmed_at": "2026-09-07T11:59:50+08:00",
            "event_type": "NR_REPAIR_CONFIRMED",
            "tags": ["NEUTRAL_REPAIR_CONFIRMED"],
            "strategy_version": "1.5.7",
        },
        "market_context": {"market_price": 79597.93, "quote": "USDT"},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "confidence": 16,
            "trade_allowed": False,
        },
        "decision_matrix": {
            "direction": "NEUTRAL",
            "decision_state": "WAIT_CONFIRMATION",
            "execution_allowed": False,
            "model_trade_support": False,
        },
        "signal_window": {
            "neutral_repair": {
                "is_active": True,
                "state": "NR_REPAIR_CONFIRMED",
            }
        },
        "reasoning": {"evidence": []},
        "conflict": {"ratio": 0.52, "level": "SEVERE"},
        "blocking": {"has_block": True, "hard_veto": None},
        "quality": {"overall": "OK"},
        "factor_cross_section": {
            "tmvf": {
                "direction": "Neutral",
                "tmv_blend": 0.0,
                "tmvf_24h": {"tmv_final": 0.0},
                "tmvf_48h": {"tmv_final": 0.0},
            },
            "micro_flow": {"combined": {"direction": "neutral"}},
            "macro_pressure": {
                "macro_score": -0.26,
                "macro_regime": "Mild Tailwind",
                "macro_shock": {"block": False, "state": "CLEAR"},
            },
            "gamma_regime": {
                "regime": "TRANSITION",
                "net_gamma_notional_usd": -0.024,
                "distance_to_pin_pct": -0.75,
            },
            "gex_info": {
                "market_state": "positive_gamma",
                "net_gamma_notional_usd": 233266219.84,
                "source_ref": "GEX_MONITOR_API",
            },
            "funding": {
                "last_rate": 0.00004431,
                "canonical_funding_semantics": {
                    "schema_name": "FundingCanonicalSemantics",
                    "schema_version": "nrd.signal.funding_semantics.v1.0.0",
                    "raw_available": True,
                    "raw_funding_rate": 0.00004431,
                    "edb_vote_allowed": False,
                    "canonical_text_cn": "温和多头费率倾向；EDB 不计票。",
                },
            },
        },
        "producer_integrity": {"record_hash": "sha256:" + card_id.lower().ljust(64, "0")[:64]},
    }
    if include_rating:
        item["signal_rating"] = signal_rating(ts_ms)
    if invalid_rating:
        item["signal_rating"] = {
            "schema": "signal_rating@1.0.0",
            "rating_scope": "side_environment_v1",
            "as_of_ms": ts_ms,
            "claims": {
                "structure": {"status": "SUPPORTED", "summary_cn": "结构可用。"},
                "put_pressure": {"status": "WIN", "summary_cn": "非法状态不能抬级。"},
                "call_pressure": {"status": "SUPPORTED", "summary_cn": "上方压力可用。"},
            },
        }
    return item


def latest_card_self_check_python():
    script = SELF_CHECK_TOOL.read_text(encoding="utf-8")
    marker = "  if python3 - \"$AUDIT_ROOT\" <<'PY'\n"
    start = script.index(marker) + len(marker)
    end = script.index("\nPY\n", start)
    return script[start:end]


def self_check_card(*, include_rating=True, strategy_version="1.6.0"):
    item = card("SELF-CHECK-CARD", 1788753590501, include_rating=include_rating)
    item["identity"]["strategy_version"] = strategy_version
    item["identity"]["is_synthetic"] = False
    item["decision_matrix"]["temporal_durability"] = "NEUTRAL"
    item["signal_window"]["session_context"] = {
        "schema_name": "SignalSessionPremiseDurabilityContext",
        "clock_window": "ASIA_ACTIVE",
        "adjustment_direction": "NEUTRAL",
        "evidence_level": "LOW",
        "backtest_delta_pp": 0.0,
        "validation_basis": {"source": "unit_test_fixture"},
        "confidence_policy": "DO_NOT_MULTIPLY_CONFIDENCE",
        "premise_durability": "NEUTRAL",
        "compat_backfill_applied": False,
    }
    funding_semantics = item["factor_cross_section"]["funding"][
        "canonical_funding_semantics"
    ]
    funding_semantics.update({
        "crowding_threshold_abs": 0.0001,
        "semantic_code": "MILD_LONG_FEE",
        "crowding_state": "NOT_CROWDED",
        "reflexivity_importance": "NOISE",
        "edb_participation": "NON_VOTING",
        "compat_backfill_applied": False,
    })
    return item


def run_latest_card_self_check(item, expected_version="1.6.0"):
    with tempfile.TemporaryDirectory() as temp_dir:
        audit_root = Path(temp_dir)
        cards_dir = audit_root / "signal_cards"
        cards_dir.mkdir()
        card_path = cards_dir / "SELF-CHECK-CARD.json"
        card_path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        manifest = {
            "cards": [{
                "card_id": "SELF-CHECK-CARD",
                "path": "signal_cards/SELF-CHECK-CARD.json",
                "summary": {"identity": item.get("identity") or {}},
            }],
        }
        (cards_dir / "index.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env["EXPECTED_SIGNAL_VERSION"] = expected_version
        env["DURABILITY_REQUIRED"] = "0"
        env.pop("TARGET_CARD_ID", None)
        env.pop("ONLY_CARD_ID", None)
        return subprocess.run(
            [sys.executable, "-c", latest_card_self_check_python(), str(audit_root)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


def valid_blind_payload():
    return {
        "theoretical_active_view": {
            "bias": "NEUTRAL_OR_RANGE",
            "conviction": "LOW",
            "basis_cn": "盲读仅形成中性区间视角。",
            "key_drivers": ["方向信息不足。"],
            "counter_evidence": ["冲突仍高。"],
            "boundary_cn": "只作审计参考。",
        },
        "gamma_regime_lens": {
            "regime": "UNKNOWN",
            "regime_extremity": "UNKNOWN",
            "dynamics_cn": "Gamma 只作空间约束。",
            "dominant_tail_risk_cn": "尾部风险无法单独确认。",
            "conviction_effect_on_directional_view": "UNKNOWN",
            "key_levels": {},
            "positioning_assumption_cn": "无法确认持仓符号。",
            "data_quality_cn": "卡内信息有限。",
            "lens_is_risk_overlay_not_direction": True,
        },
    }


def valid_reconciliation_payload(evidence_refs):
    return {
        "summary_cn": "保持人工审计观察。",
        "agreement_with_system": "SUPPORT",
        "caution_level": "MEDIUM",
        "integrated_trade_advisory": {
            "recommendation": "WAIT_FOR_CONFIRMATION",
            "final_conclusion_cn": "证据仍需确认，保持人工观察。",
            "cross_loop_rationale_cn": "评级上下文与底层事实需要一起核对。",
            "containment_assessment": {
                "state": "INCOMPLETE",
                "basis_cn": "中性接管已出现，但冲突未充分解除。",
            },
            "premium_selling_fit": {
                "state": "UNABLE_TO_JUDGE",
                "basis_cn": "候选报价与补偿尚未接入。",
            },
            "side_basis_cn": "两侧只保留环境观察，不给出具体腿。",
            "dominant_conflict_cn": "方向压力与结构支持之间仍有分歧。",
            "key_premises": [{
                "premise_cn": "评级上下文必须回到底层事实复核。",
                "evidence_refs": list(evidence_refs),
            }],
            "invalid_if": ["后续底层事实改变当前观察。"],
            "next_observation_cn": "观察冲突是否继续收敛。",
            "session_advisory": {
                "liquidity_assessment": "UNKNOWN",
                "warning_level": "INFO",
                "basis_cn": "时段信息只作背景提醒。",
            },
            "future_24h_bayesian_report": {
                "base_case": "RANGE",
                "posterior_weights_pct": {"up": 25, "down": 25, "range": 50},
                "report_cn": "24小时诊断窗仅用于后验观察，当前不生成信号寿命。",
                "key_levels": [],
                "counter_evidence_cn": ["冲突可能继续存在。"],
                "invalid_if_cn": ["底层事实发生明显变化。"],
            },
        },
        "main_supporting_factors": ["底层事实仍可追溯。"],
        "main_risks_or_conflicts": ["评级不是独立市场证据。"],
        "operator_focus": ["复核支持、反对和未知项。"],
        "invalid_if": ["卡片事实改变。"],
    }


def response(payload, profile):
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(payload, ensure_ascii=False)},
        }],
        "_api_key_route": "bearer",
        "_llm_call_routes": ["bearer"],
        "_llm_http_calls": 1,
        "_llm_call_profile": profile,
    }


def test_materializer_preserves_rating_and_manifest_summary():
    tool = load_module(MATERIALIZER_TOOL, "materialize_signal_cards_rating_summary")
    rated = card("RATED-CARD", 1788753590501, include_rating=True)
    old = card("OLD-CARD", 1788750000000)
    invalid = card("INVALID-RATING", 1788757200000, invalid_rating=True)
    bad_economics = card("BAD-ECONOMICS", 1788757300000, include_rating=True)
    bad_economics["signal_rating"]["candidate_quote_economics"] = "evaluated"
    bad_as_of = card("BAD-AS-OF", 1788757400000, include_rating=True)
    bad_as_of["signal_rating"]["as_of_ms"] = "nan"
    bad_claim_shape = card("BAD-CLAIM-SHAPE", 1788757500000, include_rating=True)
    bad_claim_shape["signal_rating"]["claims"]["structure"].pop("required_inputs")
    bad_positive_claim = card("BAD-POSITIVE-CLAIM", 1788757600000, include_rating=True)
    bad_positive_claim["signal_rating"]["claims"]["structure"]["opposition"] = [{
        "source_ref": "factor_cross_section.tmvf",
        "source_group": "TMV",
        "basis_cn": "有反对依据时不能展示为单纯支持。",
    }]
    insufficient_with_residuals = card(
        "INSUFFICIENT-RESIDUALS", 1788757700000, include_rating=True)
    insufficient_with_residuals["signal_rating"]["claims"]["call_pressure"] = {
        "status": "INSUFFICIENT",
        "summary_cn": "必要输入缺失，但已观察到的残余支持和反对仍保留。",
        "required_inputs": ["factor_cross_section.tmvf", "factor_cross_section.skew"],
        "support": [{
            "source_ref": "factor_cross_section.tmvf",
            "source_group": "TMV",
            "basis_cn": "已有局部支持，但不足以完成主张。",
        }],
        "opposition": [{
            "source_ref": "factor_cross_section.skew",
            "source_group": "SRD",
            "basis_cn": "已有局部反对，也不足以单独完成主张。",
        }],
        "unknowns": [{
            "source_ref": "factor_cross_section.skew",
            "reason_cn": "必要来源仍不完整。",
        }],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "public"
        source.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False)
                      for item in (
                          rated, old, invalid, bad_economics, bad_as_of,
                          bad_claim_shape, bad_positive_claim,
                          insufficient_with_residuals,
                      )) + "\n",
            encoding="utf-8",
        )

        tool.materialize(source, output, max_cards=10)
        cards_dir = output / "signal_cards"
        rated_card = json.loads((cards_dir / "RATED-CARD.json").read_text(encoding="utf-8"))
        invalid_card = json.loads((cards_dir / "INVALID-RATING.json").read_text(encoding="utf-8"))
        manifest = json.loads((cards_dir / "index.json").read_text(encoding="utf-8"))
        by_id = {item["card_id"]: item for item in manifest["cards"]}

        assert_true(rated_card["signal_rating"] == rated["signal_rating"],
                    "single-card JSON must preserve native signal_rating exactly")
        assert_true(invalid_card["signal_rating"] == invalid["signal_rating"],
                    "invalid native signal_rating must remain visible in the single-card trace")

        summary = by_id["RATED-CARD"]["summary"].get("signal_rating_summary")
        assert_true(set(summary) == {
            "schema", "rating_scope", "as_of_ms",
            "structure", "put_pressure", "call_pressure",
        }, "manifest rating summary exposed fields outside the frozen shape")
        for key in ("structure", "put_pressure", "call_pressure"):
            assert_true(set(summary[key]) == {"status", "summary_cn"},
                        "claim summary should only expose status and summary_cn")
        assert_true(summary["put_pressure"]["status"] == "CONFLICTED",
                    "claim status should pass through without score mapping")
        assert_true("signal_rating_summary" not in by_id["OLD-CARD"]["summary"],
                    "old cards must not receive fabricated rating summaries")
        assert_true("signal_rating_summary" not in by_id["INVALID-RATING"]["summary"],
                    "invalid rating objects must not be upgraded into manifest ratings")
        for card_id in (
                "BAD-ECONOMICS",
                "BAD-AS-OF",
                "BAD-CLAIM-SHAPE",
                "BAD-POSITIVE-CLAIM"):
            assert_true("signal_rating_summary" not in by_id[card_id]["summary"],
                        card_id + " should not produce a positive manifest summary")
        residual_summary = by_id["INSUFFICIENT-RESIDUALS"]["summary"][
            "signal_rating_summary"
        ]
        assert_true(
            residual_summary["call_pressure"]["status"] == "INSUFFICIENT"
            and residual_summary["call_pressure"]["summary_cn"].startswith(
                "必要输入缺失"),
            "INSUFFICIENT claims should retain their summary despite residual evidence",
        )


def test_signal_rating_does_not_affect_transition_materiality():
    tool = load_module(MATERIALIZER_TOOL, "materialize_signal_cards_rating_transition")
    base_without_rating = tool._transition_record(
        card("TRANSITION-A", 1788750000000),
        card("TRANSITION-B", 1788750300000),
        [],
        None,
    )
    with_rating = tool._transition_record(
        card("TRANSITION-A", 1788750000000, include_rating=True),
        card("TRANSITION-B", 1788750300000, include_rating=True),
        [],
        None,
    )

    for key in ("materiality_score", "llm_review_required", "cross_domain_flags"):
        assert_true(with_rating.get(key) == base_without_rating.get(key),
                    "signal_rating changed transition " + key)
    assert_true("signal_rating" not in json.dumps(
        with_rating.get("top_material_changes"), ensure_ascii=False),
        "signal_rating must not enter transition materiality changes")


def test_llm_packet_rating_visibility_and_blind_isolation():
    tool = load_module(LLM_TOOL, "signal_llm_review_rating_packet")
    assert_true(tool.MAIN_PROMPT_VERSION == "signal_llm_review_prompt@1.6.0",
                "main prompt version should disclose the rating-aware prompt")
    assert_true(tool.PROMPT_VERSION == tool.MAIN_PROMPT_VERSION,
                "legacy prompt alias should point to the main prompt version")
    rated = card("RATED-LLM", 1788753590501, include_rating=True)
    unrated = card("RATED-LLM", 1788753590501)

    packet = tool.build_review_packet(rated)
    catalog = {item["id"]: item for item in packet["evidence_catalog"]}
    assert_true(packet["signal_rating"] == rated["signal_rating"],
                "full review packet must carry native signal_rating")
    assert_true(catalog["EV_SIGNAL_RATING"]["pointer"] == "signal_rating",
                "full evidence catalog must make signal_rating referenceable")

    blind_with_rating = tool.build_blind_theoretical_packet(packet)
    blind_without_rating = tool.build_blind_theoretical_packet(
        tool.build_review_packet(unrated))
    assert_true(blind_with_rating == blind_without_rating,
                "rating must not change the blind packet")
    blind_prompt = tool.build_blind_prompt(packet)
    assert_true("signal_rating" not in blind_prompt,
                "blind prompt must not leak signal_rating")

    full_prompt = tool.build_prompt(packet, valid_blind_payload())
    assert_true("signal_rating" in full_prompt and "回归尚未证明" in full_prompt,
                "reconciliation prompt must see rating and the mean-reversion correction")


def test_llm_rejects_rating_as_only_market_evidence():
    tool = load_module(LLM_TOOL, "signal_llm_review_rating_validation")
    rated = card("RATED-VALIDATION", 1788753590501, include_rating=True)
    try:
        tool.build_llm_review(
            rated,
            valid_reconciliation_payload(["EV_SIGNAL_RATING"]),
            blind_payload=valid_blind_payload(),
        )
    except ValueError as exc:
        assert_true("signal_rating requires bottom evidence refs" in str(exc),
                    "unexpected validation failure: " + str(exc))
    else:
        raise AssertionError("signal_rating-only evidence refs must fail closed")

    try:
        tool.build_llm_review(
            rated,
            valid_reconciliation_payload(["EV_SIGNAL_RATING", "EV_DECISION"]),
            blind_payload=valid_blind_payload(),
        )
    except ValueError as exc:
        assert_true("signal_rating requires bottom evidence refs" in str(exc),
                    "unexpected validation failure: " + str(exc))
    else:
        raise AssertionError("signal_rating plus system decision must fail closed")

    review = tool.build_llm_review(
        rated,
        valid_reconciliation_payload(["EV_SIGNAL_RATING", "EV_TMVF"]),
        blind_payload=valid_blind_payload(),
    )
    assert_true(review["status"] == "OK"
                and review["integrated_trade_advisory"]["trade_authorization"] is False,
                "rating plus bottom evidence should remain valid audit-only output")


def test_review_generation_call_profiles_unchanged_with_rating():
    tool = load_module(LLM_TOOL, "signal_llm_review_rating_call_profiles")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "signal_llm_reviews.jsonl"
        source.write_text(
            json.dumps(card("RATED-GENERATE", 1788753590501, include_rating=True),
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        calls = []

        def fake_call(api_key, model, request_body, timeout, **kwargs):
            profile = kwargs.get("call_profile")
            calls.append({
                "profile": profile,
                "prompt": request_body["messages"][1]["content"],
                "wire_keys": sorted(tool._strip_local_request_fields(request_body)),
            })
            if profile == tool.CALL_PROFILE_MAIN_BLIND:
                return response(valid_blind_payload(), profile)
            return response(
                valid_reconciliation_payload(["EV_SIGNAL_RATING", "EV_TMVF"]),
                profile,
            )

        result = tool.generate_reviews(
            source,
            output,
            api_key="unit-test-key",
            limit=1,
            call_llm=fake_call,
            max_concurrency=1,
        )
        review = tool._read_jsonl(output)[-1]["llm_review"]

        assert_true(result["written_reviews"] == 1 and result["errors"] == 0,
                    "rated card review should complete with fake LLM")
        assert_true([call["profile"] for call in calls] == [
            tool.CALL_PROFILE_MAIN_BLIND,
            tool.CALL_PROFILE_MAIN_RECONCILIATION,
        ], "signal_rating must not add or remove main LLM calls")
        assert_true("signal_rating" not in calls[0]["prompt"]
                    and "signal_rating" in calls[1]["prompt"],
                    "rating visibility must stay reconciliation-only")
        assert_true(review["llm_call_count"] == 2
                    and review["llm_call_profiles"] == [
                        tool.CALL_PROFILE_MAIN_BLIND,
                        tool.CALL_PROFILE_MAIN_RECONCILIATION,
                    ],
                    "recorded call accounting changed")


def test_self_check_requires_native_signal_rating_for_160_production():
    valid = run_latest_card_self_check(self_check_card(include_rating=True))
    assert_true(valid.returncode == 0,
                "native 1.6 signal_rating should pass self_check: "
                + valid.stdout + valid.stderr)

    missing = run_latest_card_self_check(self_check_card(include_rating=False))
    assert_true(
        missing.returncode != 0
        and "lacks producer-native signal_rating" in (missing.stdout + missing.stderr),
        "1.6 production acceptance must reject cards without native signal_rating",
    )

    research = self_check_card(include_rating=True)
    research["provenance"] = {"source_mode": "research_replay"}
    research_result = run_latest_card_self_check(research)
    assert_true(
        research_result.returncode != 0
        and "research_replay" in (research_result.stdout + research_result.stderr),
        "research_replay cards must not satisfy 1.6 production acceptance",
    )

    synthetic = self_check_card(include_rating=True)
    synthetic["identity"]["is_synthetic"] = True
    synthetic_result = run_latest_card_self_check(synthetic)
    assert_true(
        synthetic_result.returncode != 0
        and "synthetic" in (synthetic_result.stdout + synthetic_result.stderr),
        "synthetic cards must not satisfy 1.6 production acceptance",
    )

    backfilled = self_check_card(include_rating=True)
    backfilled["signal_rating"]["compat_backfill_applied"] = True
    backfilled_result = run_latest_card_self_check(backfilled)
    assert_true(
        backfilled_result.returncode != 0
        and "compatibility backfill" in (backfilled_result.stdout + backfilled_result.stderr),
        "compatibility-backfilled signal_rating must not be accepted as native",
    )

    old_card = self_check_card(include_rating=False, strategy_version="1.5.7")
    old_result = run_latest_card_self_check(old_card, expected_version="1.5.7")
    assert_true(old_result.returncode == 0,
                "explicit 1.5.7 historical acceptance should not require signal_rating: "
                + old_result.stdout + old_result.stderr)


def main():
    test_materializer_preserves_rating_and_manifest_summary()
    test_signal_rating_does_not_affect_transition_materiality()
    test_llm_packet_rating_visibility_and_blind_isolation()
    test_llm_rejects_rating_as_only_market_evidence()
    test_review_generation_call_profiles_unchanged_with_rating()
    test_self_check_requires_native_signal_rating_for_160_production()
    print("signal_rating_pipeline: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_rating_pipeline: FAIL - " + str(exc))
        raise
