import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_TOOL = ROOT / "tools" / "signal_llm_review.py"
ENTRY_TOOL = ROOT / "tools" / "signal_llm_review_entry.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def required_input(source_ref, group, usable=True, status="OK", reason_cn="OK"):
    return {
        "source_ref": source_ref,
        "source_group": group,
        "usable": usable,
        "status": status,
        "reason_cn": reason_cn,
        "impact_cn": "只影响该项评级主张。",
    }


def native_claim(status="SUPPORTED", *, required=None):
    return {
        "status": status,
        "summary_cn": "该主张按当前来源形成可读观察。",
        "required_inputs": list(required or []),
        "support": [{
            "source_ref": "factor_cross_section.tmvf",
            "source_group": "TMVF",
            "basis_cn": "量价主干给出可用事实。",
        }],
        "opposition": [],
        "unknowns": [{
            "source_ref": "factor_cross_section.funding",
            "reason_cn": "资金费率为有效非投票观察，不是缺失。",
        }],
    }


def card(tool, card_id="COMFORT", *, lean="BULLISH", support_label="TRADE_SUPPORT_WEAK",
         decision_state="APPROVABLE", has_block=False, hard_veto=None,
         window_active=True, nr_state="NR_REPAIR_CONFIRMED", quality_sources=None,
         structure_status="SUPPORTED", put_status="SUPPORTED", call_status="SUPPORTED",
         put_required=None, call_required=None):
    as_of_ms = 1788753590501
    base_required = [
        required_input("factor_cross_section.anchor", "Anchor"),
        required_input("factor_cross_section.gamma_regime", "GGR"),
    ]
    pressure_required = [
        required_input("factor_cross_section.tmvf", "TMVF"),
        required_input("factor_cross_section.micro_flow", "CVD"),
        required_input("factor_cross_section.macro_pressure", "MACRO"),
        required_input("factor_cross_section.funding", "Funding"),
        required_input("factor_cross_section.skew", "SRD"),
    ]
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_time_ms": as_of_ms,
            "confirmed_at": "2026-09-07T11:59:50+08:00",
        },
        "market_context": {"price": 79597.93, "quote_currency": "USDT"},
        "decision": {
            "lean": lean,
            "support_label": support_label,
            "trade_allowed": False,
        },
        "decision_matrix": {
            "direction": lean,
            "decision_state": decision_state,
            "execution_allowed": False,
        },
        "blocking": {
            "has_block": has_block,
            "hard_veto": hard_veto,
            "block_kind": "HARD" if hard_veto else None,
            "soft_gates": [{"gate": "WAIT"}] if support_label == "WAIT_CONFIRMATION" else [],
        },
        "quality": {"overall": "OK", "sources": dict(quality_sources or {})},
        "signal_window": {"nr_state": nr_state, "is_active": window_active},
        "factor_cross_section": {
            "anchor": {
                "score": 72.0,
                "gex_source_ts_ms": as_of_ms - 60000,
                "freshness": "FRESH",
            },
            "tmvf": {"direction": "Bullish", "tmv_blend": 0.42},
            "micro_flow": {"combined": {"direction": "bullish"}},
            "macro_pressure": {"macro_score": 0.18, "macro_shock": {"block": False}},
            "gamma_regime": {"regime": "positive_gamma", "call_wall": 82000, "put_wall": 78000},
            "gex_info": {"market_state": "positive_gamma", "net_gamma_notional_usd": 233266219.84},
            "skew": {"vote": 0.12},
            "funding": {
                "last_rate": 0.00005,
                "canonical_funding_semantics": tool.build_funding_semantics(
                    0.00005,
                    source="unit_test:funding",
                    compat_backfill_applied=False,
                ),
            },
        },
        "signal_rating": {
            "schema": "signal_rating@1.0.0",
            "rating_scope": "side_environment_v1",
            "candidate_quote_economics": "not_evaluated",
            "as_of_ms": as_of_ms,
            "claims": {
                "structure": native_claim(structure_status, required=base_required),
                "put_pressure": native_claim(
                    put_status, required=put_required or pressure_required),
                "call_pressure": native_claim(
                    call_status, required=call_required or pressure_required),
            },
            "market_state": {
                "legacy_label": "Anchor Mean-Reversion",
                "interpretation_cn": "旧标签表示 TMVF 方向中性，回归尚未证明。",
            },
            "context": {"nr_active": window_active},
            "model_constraints": {"execution_allowed": False},
        },
    }


def blind_payload(bias="BULLISH_LEAN"):
    return {
        "theoretical_active_view": {
            "bias": bias,
            "conviction": "MEDIUM",
            "basis_cn": "盲读仅说明卡内结构方向，仍保留反证。",
            "key_drivers": ["量价与结构事实可用。"],
            "counter_evidence": ["宏观背景仍需观察。"],
            "boundary_cn": "只作审计参考，不改变系统结论。",
        },
        "gamma_regime_lens": {
            "regime": "LONG_GAMMA_STABILIZING",
            "regime_extremity": "MEDIUM",
            "dynamics_cn": "Gamma 只作为空间与尾部风险覆盖。",
            "dominant_tail_risk_cn": "主要风险是结构约束失效。",
            "conviction_effect_on_directional_view": "NEUTRAL",
            "key_levels": {"call_wall": 82000, "put_wall": 78000},
            "positioning_assumption_cn": "GEX 符号假设仍需按卡内来源理解。",
            "data_quality_cn": "卡内 GEX 来源可用。",
            "lens_is_risk_overlay_not_direction": True,
        },
    }


def future_report(price=79597.93, base_case="UP"):
    weights = (
        {"up": 52, "down": 18, "range": 30}
        if base_case == "UP"
        else {"up": 20, "down": 52, "range": 28}
    )
    if base_case == "RANGE":
        weights = {"up": 25, "down": 25, "range": 50}
    return {
        "base_case": base_case,
        "posterior_weights_pct": weights,
        "report_cn": "24小时诊断窗只用于路径观察，当前权重不是胜率。",
        "key_levels": [{
            "price": price,
            "role_cn": "卡内现价观察位",
            "source_type": "PACKET_OBSERVED",
            "basis_cn": "精确来自卡内现价。",
        }],
        "counter_evidence_cn": ["若反向压力增强则削弱基准解释。"],
        "invalid_if_cn": ["关键结构事实失效。"],
    }


def side(grade, refs, *, counter_refs=None, unresolved=None, s_basis="",
         s_refs=None, basis=None):
    return {
        "grade": grade,
        "basis_cn": basis or "该侧信号层机制与当前结构相容。",
        "counter_evidence_cn": "主要反证已经单列，暂未推翻该侧机制。",
        "unresolved_conditions_cn": list(unresolved or []),
        "next_observation_cn": "继续观察反向压力是否扩大。",
        "evidence_refs": list(refs),
        "counter_evidence_refs": list(counter_refs or []),
        "s_upgrade_basis_cn": s_basis,
        "s_upgrade_evidence_refs": list(s_refs or []),
    }


def payload(put_side, call_side, *, recommendation="SELL_PUT_SPREAD_REVIEW",
            base_case="UP", price=79597.93):
    return {
        "summary_cn": "保持人工审计复核。",
        "agreement_with_system": "SUPPORT",
        "caution_level": "LOW",
        "integrated_trade_advisory": {
            "recommendation": recommendation,
            "final_conclusion_cn": "结构复核只给出信号层观察。",
            "cross_loop_rationale_cn": "量价、空间与反证共同形成当前判断。",
            "containment_assessment": {
                "state": "ESTABLISHED",
                "basis_cn": "中性接管或方向结构已有可核对事实。",
            },
            "premium_selling_fit": {
                "state": "FIT",
                "basis_cn": "卖方结构的环境问题可先独立复核。",
            },
            "side_basis_cn": "当前只讨论侧别环境，不讨论具体报价。",
            "dominant_conflict_cn": "反证保留为观察条件。",
            "key_premises": [{
                "premise_cn": "底层市场事实支持当前审计判断。",
                "evidence_refs": ["EV_TMVF", "EV_GEX"],
            }],
            "invalid_if": ["底层事实发生反向变化。"],
            "next_observation_cn": "观察反向压力是否扩张。",
            "session_advisory": {
                "liquidity_assessment": "ALIGNED",
                "warning_level": "INFO",
                "basis_cn": "时段只作背景提醒。",
            },
            "future_24h_bayesian_report": future_report(price=price, base_case=base_case),
            "side_comfort_ratings": {
                "put_credit": put_side,
                "call_credit": call_side,
            },
        },
        "main_supporting_factors": ["市场事实可核验。"],
        "main_risks_or_conflicts": ["反证仍需观察。"],
        "operator_focus": ["复核支持、反对和未知。"],
        "invalid_if": ["卡片事实改变。"],
    }


def comfort(review):
    return review["integrated_trade_advisory"]["side_comfort_ratings"]


def test_schema_prompt_anchor_and_blind_contract(core):
    c = card(core, "ANCHOR-CONTRACT")
    packet = core.build_review_packet(c)
    catalog = {item["id"]: item for item in packet["evidence_catalog"]}
    blind = core.build_blind_theoretical_packet(packet)
    request_schema = core.build_chat_request("x")["_local_json_schema"]
    comfort_schema = request_schema["properties"]["integrated_trade_advisory"][
        "properties"]["side_comfort_ratings"]

    assert_true(core.OUTPUT_SCHEMA_VERSION == "signal_llm_review@1.6.0",
                "review schema version mismatch")
    assert_true(core.MAIN_PROMPT_VERSION == "signal_llm_review_prompt@1.6.0",
                "main prompt version mismatch")
    assert_true(packet["anchor"] == c["factor_cross_section"]["anchor"],
                "full packet must expose raw Anchor facts")
    assert_true(catalog["EV_ANCHOR"]["pointer"] == "anchor",
                "Anchor evidence id must point to the full-packet Anchor object")
    assert_true("anchor" not in json.dumps(blind, ensure_ascii=False),
                "blind packet must not inherit the full Anchor object")
    assert_true(set(comfort_schema["required"]) == {"put_credit", "call_credit"},
                "model comfort schema must only require side objects")


def test_a_grade_survives_readonly_no_quote_and_conflicted_native(core):
    c = card(core, "A-CONFLICT", put_status="CONFLICTED")
    review = core.build_llm_review(
        c,
        payload(
            side("A", ["EV_TMVF", "EV_GEX"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    ratings = comfort(review)
    assert_true(ratings["put_credit"]["final_grade"] == "A",
                "conflicted but usable native facts should not automatically deny A")
    assert_true(ratings["candidate_quote_economics"] == "not_evaluated",
                "missing quotes must stay outside signal-layer grade")
    assert_true(ratings["put_credit"]["cap_reasons_cn"] == [],
                "read-only execution permission must not downgrade the side")


def test_wait_and_hard_block_repair_recompute_final_grade(entry):
    c = card(
        entry.core,
        "BLOCK-CAP",
        has_block=True,
        hard_veto={"veto_reason": "TEST", "zh": "测试阻断"},
    )
    review = entry.build_llm_review(
        c,
        payload(
            side("A", ["EV_TMVF", "EV_GEX"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    ratings = comfort(review)
    policy = review["integrated_trade_advisory"]["policy_validation"]
    assert_true(policy["hard_block_recommendation_repair_applied"] is True,
                "entry hard-block repair must run before comfort finalization")
    assert_true(ratings["put_credit"]["model_grade"] == "A"
                and ratings["put_credit"]["final_grade"] == "B",
                "hard-block repaired cards must not retain A/S comfort")

    wait_card = card(entry.core, "WAIT-CAP", support_label="WAIT_CONFIRMATION",
                     decision_state="WAIT_CONFIRMATION")
    wait_review = entry.core.build_llm_review(
        wait_card,
        payload(
            side("A", ["EV_TMVF", "EV_GEX"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(wait_review)["put_credit"]["final_grade"] == "B",
                "WAIT cards must cap A/S at B without becoming D")


def test_invalid_ref_is_side_local_unrated(core):
    c = card(core, "SIDE-REF", lean="NEUTRAL")
    review = core.build_llm_review(
        c,
        payload(
            side("A", ["EV_NOT_REAL"]),
            side("A", ["EV_TMVF", "EV_GEX"]),
            recommendation="NEUTRAL_SINGLE_SIDE_REVIEW",
            base_case="RANGE",
        ),
        blind_payload=blind_payload("NEUTRAL_OR_RANGE"),
    )
    ratings = comfort(review)
    assert_true(ratings["put_credit"]["status"] == "UNRATED"
                and ratings["put_credit"]["final_grade"] is None,
                "invalid refs must make only that side unrated")
    assert_true(ratings["call_credit"]["final_grade"] == "A",
                "the other side should keep its valid grade")
    assert_true(ratings["headline"]["focus_side"] == "call_credit",
                "headline should point to the remaining valid side")
    assert_true("EV_NOT_REAL" not in json.dumps(ratings["put_credit"], ensure_ascii=False),
                "invalid raw ref must not leak through the display side")


def test_usable_insufficient_claim_can_still_rate_b_or_c(core):
    c = card(core, "INSUFFICIENT-B", structure_status="INSUFFICIENT")
    review = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_TMVF", "EV_GEX"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    ratings = comfort(review)
    assert_true(ratings["put_credit"]["final_grade"] == "B"
                and ratings["call_credit"]["final_grade"] == "C",
                "usable transition structure must not become unrated just because status is insufficient")
    assert_true("资金费率为有效非投票观察" in c["signal_rating"]["claims"][
        "put_pressure"]["unknowns"][0]["reason_cn"],
                "fixture must keep non-voting unknown distinct from missing")


def test_missing_required_input_unrated_but_keeps_valid_text(core):
    missing_flow = [
        required_input("factor_cross_section.tmvf", "TMVF"),
        required_input(
            "factor_cross_section.micro_flow",
            "CVD",
            usable=False,
            status="MISSING",
            reason_cn="主动买卖流当前缺少可用样本。",
        ),
    ]
    c = card(core, "MISSING-SOURCE", put_required=missing_flow)
    review = core.build_llm_review(
        c,
        payload(
            side("A", ["EV_TMVF", "EV_GEX"], basis="Put 侧已有局部有效观察。"),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(review)["put_credit"]
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "missing required source must produce unrated, not a low grade")
    assert_true("Put 侧已有局部有效观察" in put_rating["basis_cn"],
                "valid local observation text should survive a missing-source cap")
    assert_true(any("主动买卖流" in item for item in put_rating["cap_reasons_cn"]),
                "cap reason should use a Chinese source label")


def test_s_grade_requires_extra_nonduplicate_and_current_basis(core):
    c = card(core, "S-RULE")
    same_group = core.build_llm_review(
        c,
        payload(
            side(
                "S",
                ["EV_GEX"],
                s_basis="同一结构来源继续支持优先处理。",
                s_refs=["EV_GAMMA"],
            ),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(same_group)["put_credit"]["final_grade"] == "A",
                "S must downgrade when upgrade refs repeat the same source group")

    extra = core.build_llm_review(
        c,
        payload(
            side(
                "S",
                ["EV_GEX"],
                s_basis="资金费率提供额外非重复依据，且主要失效条件是拥挤状态反转。",
                s_refs=["EV_FUNDING"],
            ),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(extra)["put_credit"]["final_grade"] == "S",
                "S should remain available without point-by-point path data")

    future = core.build_llm_review(
        c,
        payload(
            side(
                "S",
                ["EV_GEX"],
                s_basis="30分钟后行情继续有利，因此升级。",
                s_refs=["EV_FUNDING"],
            ),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(future)["put_credit"]["final_grade"] == "A",
                "future path wording cannot upgrade the current card to S")


def test_rating_only_support_is_unrated_not_low_grade(core):
    c = card(core, "RATING-ONLY")
    review = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_SIGNAL_RATING"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(review)["put_credit"]
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "producer rating alone must not become B or be disguised as C/D")


def test_raw_comfort_text_is_unrated_and_sanitized(core):
    c = card(core, "RAW-TEXT")
    review = core.build_llm_review(
        c,
        payload(
            side("A", ["EV_TMVF"], basis="decision_matrix.window=CONFIRMED。"),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(review)["put_credit"]
    serialized = json.dumps(put_rating, ensure_ascii=False)
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "raw machine text should invalidate that side comfort rating")
    assert_true("decision_matrix" not in serialized and "CONFIRMED" not in serialized,
                "unsafe raw text must not remain in the displayable comfort side")


def test_quality_is_gate_not_market_vote_or_s_upgrade(core):
    c = card(core, "QUALITY-GATE")
    quality_only = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_QUALITY"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    put_quality = comfort(quality_only)["put_credit"]
    assert_true(put_quality["status"] == "UNRATED"
                and put_quality["final_grade"] is None,
                "quality evidence alone must not become a B market opportunity")

    quality_upgrade = core.build_llm_review(
        c,
        payload(
            side(
                "S",
                ["EV_TMVF"],
                s_basis="数据质量只能说明可用，不能作为额外市场事实。",
                s_refs=["EV_QUALITY"],
            ),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(quality_upgrade)["put_credit"]["final_grade"] == "A",
                "quality must not provide the nonduplicate market fact required for S")


def test_unavailable_or_future_referenced_fact_is_unrated(core):
    unavailable_funding = card(
        core,
        "FUNDING-UNAVAILABLE",
        quality_sources={
            "funding": {
                "status": "UNAVAILABLE",
                "reason_cn": "资金费率当前不可用。",
            }
        },
    )
    unavailable_funding["factor_cross_section"]["funding"] = {
        "last_rate": None,
        "canonical_funding_semantics": core.build_funding_semantics(
            None,
            source="unit_test:funding",
            compat_backfill_applied=False,
        ),
    }
    funding_review = core.build_llm_review(
        unavailable_funding,
        payload(
            side(
                "S",
                ["EV_TMVF"],
                s_basis="另一路来源给出额外支持。",
                s_refs=["EV_FUNDING"],
            ),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    funding_rating = comfort(funding_review)["put_credit"]
    assert_true(funding_rating["status"] == "UNRATED"
                and funding_rating["final_grade"] is None,
                "unavailable referenced funding must be unrated, not downgraded to A/D")
    assert_true(any("资金费率" in item for item in funding_rating["cap_reasons_cn"]),
                "unavailable funding reason should stay in Chinese market language")

    future_ref = card(core, "FUTURE-REF")
    future_ref["factor_cross_section"]["tmvf"]["observed_at"] = "2099-01-01T00:00:00+00:00"
    future_review = core.build_llm_review(
        future_ref,
        payload(
            side("A", ["EV_TMVF"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    future_rating = comfort(future_review)["put_credit"]
    assert_true(future_rating["status"] == "UNRATED"
                and future_rating["final_grade"] is None,
                "future-dated referenced evidence must invalidate that side rating")


def test_window_unopened_keeps_b_but_expired_becomes_d(core):
    unopened = card(
        core,
        "WINDOW-UNOPENED",
        window_active=False,
        nr_state="NR_WAIT_ANCHOR_REPAIR",
    )
    unopened_review = core.build_llm_review(
        unopened,
        payload(
            side("B", ["EV_TMVF"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(unopened_review)["put_credit"]["final_grade"] == "B",
                "unopened/pending window should keep a model B as attention start")

    expired = card(
        core,
        "WINDOW-EXPIRED",
        window_active=False,
        nr_state="NR_REPAIR_STALE",
    )
    expired_review = core.build_llm_review(
        expired,
        payload(
            side("B", ["EV_TMVF"]),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    expired_rating = comfort(expired_review)["put_credit"]
    assert_true(expired_rating["status"] == "RATED"
                and expired_rating["final_grade"] == "D",
                "explicit expired/stale window should become a valid D avoidance")
    assert_true(any("窗口" in item or "失效" in item for item in expired_rating["cap_reasons_cn"]),
                "expired-window D should explain the lifecycle reason in Chinese")


def test_comfort_forbids_strike_expiry_contract_terms(core):
    c = card(core, "CONTRACT-DETAIL")
    review = core.build_llm_review(
        c,
        payload(
            side("A", ["EV_TMVF"], basis="卖出行权价相当于当前锚下沿，expiry 等待人工确认。"),
            side("C", ["EV_MACRO"]),
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(review)["put_credit"]
    serialized = json.dumps(put_rating, ensure_ascii=False)
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "comfort human fields must not mention strike/expiry contract details")
    assert_true("行权价" not in serialized and "expiry" not in serialized.lower(),
                "forbidden contract-detail text must be wiped from displayable comfort side")
    assert_true(any("合约" in item or "期限" in item for item in put_rating["cap_reasons_cn"]),
                "contract-detail rejection should explain the comfort-only boundary")


def test_gamma_flip_real_wrong_above_claim_is_unrated(core):
    c = card(core, "REAL-FLIP-SNIPPET")
    c["market_context"]["price"] = 79759.47
    c["factor_cross_section"]["gamma_regime"]["flip_point"] = 79775.2848902611
    c["factor_cross_section"]["gex_info"]["market_state"] = "positive_gamma"
    review = core.build_llm_review(
        c,
        payload(
            side(
                "B",
                ["EV_TMVF", "EV_GEX"],
                basis="Put 信用价差由 TMVF、宏观和偏斜支持，且在 Gamma 反转点上方的正 Gamma 状态提高稳定性。",
            ),
            side("C", ["EV_MACRO"]),
            price=79759.47,
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(review)["put_credit"]
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "price below gamma flip must not be described as above in basis_cn")
    assert_true(any("低于 Gamma 反转点" in item for item in put_rating["cap_reasons_cn"]),
                "wrong gamma-flip position should produce a specific Chinese reason")


def test_gamma_flip_correct_position_and_condition_sentence_survive(core):
    c = card(core, "FLIP-CORRECT")
    c["market_context"]["price"] = 79759.47
    c["factor_cross_section"]["gamma_regime"]["flip_point"] = 79775.2848902611
    correct = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_TMVF"], basis="当前价格在 Gamma 反转点下方，TMVF 仍给出该侧观察依据。"),
            side("C", ["EV_MACRO"]),
            price=79759.47,
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(correct)["put_credit"]["final_grade"] == "B",
                "correct below-flip current position should not be rejected")

    conditional = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_TMVF"], basis="如果价格后续站上 Gamma 反转点上方，需要重新确认机制；当前只保留 TMVF 观察。"),
            side("C", ["EV_MACRO"]),
            price=79759.47,
        ),
        blind_payload=blind_payload(),
    )
    assert_true(comfort(conditional)["put_credit"]["final_grade"] == "B",
                "future/conditional gamma-flip wording in basis should not be treated as a current fact")


def test_gamma_flip_reverse_mirror_and_s_basis_check(core):
    c = card(core, "FLIP-MIRROR")
    c["market_context"]["price"] = 79810.0
    c["factor_cross_section"]["gamma_regime"]["flip_point"] = 79775.2848902611
    below_wrong = core.build_llm_review(
        c,
        payload(
            side("B", ["EV_TMVF"], basis="当前价格在 Gamma 反转点下方，因此结构更舒服。"),
            side("C", ["EV_MACRO"]),
            price=79810.0,
        ),
        blind_payload=blind_payload(),
    )
    put_rating = comfort(below_wrong)["put_credit"]
    assert_true(put_rating["status"] == "UNRATED"
                and put_rating["final_grade"] is None,
                "price above gamma flip must not be described as below in basis_cn")
    assert_true(any("高于 Gamma 反转点" in item for item in put_rating["cap_reasons_cn"]),
                "reverse gamma-flip mismatch should produce a specific Chinese reason")

    s_wrong = core.build_llm_review(
        c,
        payload(
            side(
                "S",
                ["EV_TMVF"],
                s_basis="额外依据是当前价格在 Gamma 反转点下方且 Funding 同向。",
                s_refs=["EV_FUNDING"],
            ),
            side("C", ["EV_MACRO"]),
            price=79810.0,
        ),
        blind_payload=blind_payload(),
    )
    s_rating = comfort(s_wrong)["put_credit"]
    assert_true(s_rating["status"] == "UNRATED"
                and s_rating["final_grade"] is None,
                "s_upgrade_basis_cn current gamma-flip mismatch must invalidate the side")


def test_default_error_comfort_text_is_chinese_and_raw_error_preserved(core):
    safe_error = "invalid gamma_regime_lens.regime"
    record = core._card_error_record(
        "ERR-CARD", core.DEFAULT_MODEL, "2026-09-07T00:00:00+08:00",
        safe_error, ValueError(safe_error))
    review = record["llm_review"]
    ratings = review["integrated_trade_advisory"]["side_comfort_ratings"]
    display_reason = "暂未完成有效复核，保留本卡市场观察。"

    for side_key in ("put_credit", "call_credit"):
        side_rating = ratings[side_key]
        serialized = json.dumps(side_rating, ensure_ascii=False)
        assert_true(side_rating["status"] == "UNRATED"
                    and side_rating["final_grade"] is None,
                    "default comfort error side must stay unrated")
        assert_true(display_reason in side_rating["basis_cn"],
                    "default comfort basis should use fixed Chinese reader text")
        assert_true(display_reason in side_rating["counter_evidence_cn"],
                    "default comfort counter evidence should use fixed Chinese reader text")
        assert_true(side_rating["cap_reasons_cn"] == [display_reason],
                    "default comfort cap reason should not expose raw failure text")
        assert_true("LLM call or parsing failed" not in serialized
                    and safe_error not in serialized,
                    "comfort rating must not leak raw LLM failure text")

    assert_true(review["failure_state"]["type"] == "VALIDATION_ERROR",
                "raw failure state should remain available for audit download")
    assert_true(review["main_risks_or_conflicts"] == [
        "LLM call or parsing failed: " + safe_error
    ], "raw LLM error should remain in diagnostic risks")

def main():
    core = load(CORE_TOOL, "signal_llm_review_comfort_core")
    entry = load(ENTRY_TOOL, "signal_llm_review_comfort_entry")
    test_schema_prompt_anchor_and_blind_contract(core)
    test_a_grade_survives_readonly_no_quote_and_conflicted_native(core)
    test_wait_and_hard_block_repair_recompute_final_grade(entry)
    test_invalid_ref_is_side_local_unrated(core)
    test_usable_insufficient_claim_can_still_rate_b_or_c(core)
    test_missing_required_input_unrated_but_keeps_valid_text(core)
    test_s_grade_requires_extra_nonduplicate_and_current_basis(core)
    test_rating_only_support_is_unrated_not_low_grade(core)
    test_raw_comfort_text_is_unrated_and_sanitized(core)
    test_quality_is_gate_not_market_vote_or_s_upgrade(core)
    test_unavailable_or_future_referenced_fact_is_unrated(core)
    test_window_unopened_keeps_b_but_expired_becomes_d(core)
    test_comfort_forbids_strike_expiry_contract_terms(core)
    test_gamma_flip_real_wrong_above_claim_is_unrated(core)
    test_gamma_flip_correct_position_and_condition_sentence_survive(core)
    test_gamma_flip_reverse_mirror_and_s_basis_check(core)
    test_default_error_comfort_text_is_chinese_and_raw_error_preserved(core)
    print("signal_comfort_review: PASS")


if __name__ == "__main__":
    main()
