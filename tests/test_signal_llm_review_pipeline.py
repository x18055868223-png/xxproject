import importlib.util
import json
import pathlib
import socket
import tempfile
import urllib.error


ROOT = pathlib.Path(__file__).resolve().parents[1]
GEMINI_TOOL = ROOT / "tools" / "gemini_signal_llm_review.py"
MATERIALIZER_TOOL = ROOT / "tools" / "materialize_signal_cards.py"
FRONTEND_APP = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"
FRONTEND_HTML = ROOT / "deploy" / "signal_audit" / "frontend" / "index.html"
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def card(card_id="20260619T000000+0800-BTC-GEMINI-LOCAL-PREVIEW"):
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_at": "2026-06-19T00:00:00+08:00",
            "is_synthetic": False,
        },
        "market_context": {"price": 64000, "quote_currency": "USDT"},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "confidence": 38,
            "confidence_semantics": "EVIDENCE_QUALITY_NOT_WIN_RATE",
            "trade_allowed": False,
        },
        "reasoning": {
            "evidence": [
                {"key": "TMV", "lean": "BULLISH"},
                {"key": "SRD", "lean": "BEARISH"},
            ],
        },
        "conflict": {"ratio": 0.38, "level": "MATERIAL"},
        "quality": {"overall": "OK"},
        "factor_cross_section": {
            "gex_info": {
                "market_state": "POSITIVE_GAMMA",
                "net_gamma_notional_usd": 12400000,
                "flip_point": 62800,
                "call_wall": 65000,
                "put_wall": 60000,
                "magnet_level": 64000,
                "rank": {
                    "metrics": {
                        "gex_board.total_net_gex": {
                            "rank_pct": 61,
                            "quality": "warming_up",
                        },
                    },
                },
            },
        },
        "delivery": {"local_jsonl": r"C:\should\not\leave.jsonl"},
        "api_token": "sk-should-not-leave",
    }


def transition_record():
    return {
        "schema_name": "SignalTransitionRecord",
        "schema_version": "signal_transition_record@1.0.0",
        "transition_id": "tr-CARD-A-CARD-B",
        "symbol": "BTC",
        "previous_card_id": "CARD-A",
        "current_card_id": "CARD-B",
        "previous_ts_ms": 1781770200000,
        "current_ts_ms": 1781773800000,
        "elapsed_ms": 3600000,
        "relation": {
            "comparison_quality": "HIGH",
            "comparison_limitations": [],
        },
        "decision_transition": {
            "lean_before": "BULLISH_STRONG",
            "lean_after": "NEUTRAL",
            "support_before": "TRADE_SUPPORT_STRONG",
            "support_after": "NO_TRADE_BLOCKED",
            "block_entered": True,
        },
        "top_material_changes": [
            {
                "domain": "MACRO",
                "field": "factor_cross_section.macro_pressure.macro_score",
                "previous": 0.0309,
                "current": 0.4588,
                "delta_abs": 0.4279,
                "materiality": "CRITICAL",
                "meaning": "MORE_RISK_HEADWIND",
            },
            {
                "domain": "MACRO",
                "field": "factor_cross_section.macro_pressure.components.US10Y.scoring_bps",
                "previous": -1.8,
                "current": 6.2,
                "delta_abs": 8.0,
                "sign_flip": True,
                "materiality": "HIGH",
                "meaning": "RISK_HEADWIND_SIGN_FLIP",
            },
        ],
        "core_skeleton": {
            "schema_version": "transition_core_skeleton@1.0.0",
            "timeline": {
                "previous_card_id": "CARD-A",
                "current_card_id": "CARD-B",
                "elapsed_ms": 3600000,
                "comparison_quality": "HIGH",
            },
            "domains": [
                {
                    "domain": "TMV",
                    "previous": {"tmv_blend": 0.42, "tmvf_24h_final": 0.31},
                    "current": {"tmv_blend": 0.18, "tmvf_24h_final": 0.11},
                    "source_refs": ["factor_cross_section.tmvf"],
                },
                {
                    "domain": "MACRO",
                    "previous": {"macro_score": 0.0309},
                    "current": {"macro_score": 0.4588},
                    "source_refs": ["factor_cross_section.macro_pressure"],
                },
                {
                    "domain": "FUNDING",
                    "previous": {"last_rate": 0.000015},
                    "current": {"last_rate": 0.000054},
                    "source_refs": ["factor_cross_section.funding"],
                },
                {
                    "domain": "GAMMA",
                    "previous": {"net_gamma_notional_usd": 12400000.0},
                    "current": {"net_gamma_notional_usd": -7600000.0},
                    "source_refs": ["factor_cross_section.gamma_regime"],
                },
                {
                    "domain": "SKEW",
                    "previous": {"rr_blend": -0.05},
                    "current": {"rr_blend": -0.12},
                    "source_refs": ["factor_cross_section.skew"],
                },
                {
                    "domain": "P_C_RATIO",
                    "previous": {"put_call_ratio": 0.92},
                    "current": {"put_call_ratio": 1.22},
                    "source_refs": ["factor_cross_section.gex_info"],
                },
                {
                    "domain": "CONFLICT",
                    "previous": {"ratio": 0.18, "level": "LOW"},
                    "current": {"ratio": 0.62, "level": "MATERIAL"},
                    "source_refs": ["conflict"],
                },
            ],
        },
        "domain_change_summaries": [
            {
                "domain": "MACRO",
                "materiality": "CRITICAL",
                "raw_change_count": 2,
                "primary_fields": [
                    "factor_cross_section.macro_pressure.macro_score",
                    "factor_cross_section.macro_pressure.components.US10Y.scoring_bps",
                ],
                "source_refs": ["factor_cross_section.macro_pressure"],
                "children": [],
            },
            {
                "domain": "FUNDING",
                "materiality": "HIGH",
                "raw_change_count": 1,
                "primary_fields": ["factor_cross_section.funding.last_rate"],
                "source_refs": ["factor_cross_section.funding"],
                "children": [],
            },
            {
                "domain": "GAMMA",
                "materiality": "HIGH",
                "raw_change_count": 1,
                "primary_fields": ["factor_cross_section.gamma_regime.net_gamma_notional_usd"],
                "source_refs": ["factor_cross_section.gamma_regime"],
                "children": [],
            },
        ],
        "core_transition_display": [
            {
                "domain": "DECISION",
                "title_cn": "决策（状态/置信）",
                "value_key": "confidence",
                "previous_display": "58",
                "current_display": "42",
                "delta_display": "-16",
                "meaning_cn": "维持中性偏好，持续受宏观硬阻断；置信下降说明审计证据质量收缩，不是胜率变化。",
                "grade_cn": "高",
                "source_note": "display-only semantic layer",
            },
            {
                "domain": "FUNDING",
                "title_cn": "Funding（期货资金费率）",
                "value_key": "last_rate",
                "previous_display": "0.00038%",
                "current_display": "-0.001438%",
                "delta_display": "-0.001818%",
                "meaning_cn": "资金费率由轻微正值转为轻微负值，说明永续端多头付费压力消失，方向意义偏弱。",
                "grade_cn": "低",
                "source_note": "raw last_rate",
            },
            {
                "domain": "P_C_RATIO",
                "title_cn": "P/C（期权需求）",
                "value_key": "put_call_ratio",
                "previous_display": "2.29",
                "current_display": "2.18",
                "delta_display": "-0.11",
                "meaning_cn": "保护需求高位略回落，但仍偏高，不构成方向反转。",
                "grade_cn": "低",
                "source_note": "ratio display",
            },
        ],
        "raw_change_groups": [
            {
                "domain": "MACRO",
                "raw_change_count": 2,
                "children": [],
            },
        ],
        "trajectory": {
            "recent_event_count": 2,
            "macro_direction": "DETERIORATING",
            "funding_direction": "CROWDING_UP",
        },
        "domain_states": {
            "MACRO": "SHOCK",
            "FUNDING": "RISING_NON_VOTING",
        },
        "cross_domain_flags": [
            "DECISION_SUPPORT_COLLAPSE",
            "MACRO_SHOCK",
            "MULTI_DOMAIN_RISK_DETERIORATION",
        ],
        "materiality_score": 91.0,
        "llm_review_required": True,
        "record_hash": "sha256:transition",
    }


def model_payload():
    return {
        "summary_cn": "系统结论为方向中性，复核意见认为等待确认合理。",
        "agreement_with_system": "SUPPORT",
        "caution_level": "LOW",
        "theoretical_active_view": {
            "bias": "MIXED_UNCLEAR",
            "conviction": "LOW",
            "basis_cn": "从理论截面看，TMV 与主动流偏多，但宏观和偏斜压制仍在，主动倾向只能给混合不明。",
            "key_drivers": ["TMV 与 CVD 提供同向主动支撑。"],
            "counter_evidence": ["宏观压力和期权偏斜构成反向证据。"],
            "boundary_cn": "该判断只作为审计参考，不改变系统信号、门控或交易许可。",
        },
        "gamma_regime_lens": {
            "regime": "LONG_GAMMA_STABILIZING",
            "regime_extremity": "MEDIUM",
            "dynamics_cn": "正 Gamma 体制更偏波动压制和 pin 附近均值回归。",
            "dominant_tail_risk_cn": "主要风险是把钉住误读成趋势突破。",
            "conviction_effect_on_directional_view": "LOWER",
            "key_levels": {
                "flip": 62800,
                "call_wall": 65000,
                "put_wall": 60000,
                "pin": 64000,
            },
            "positioning_assumption_cn": "该判断假设 netGEX 符号可代理做市商 Gamma 暴露；加密市场该假设未必稳定。",
            "data_quality_cn": "rank 仍处于 warming_up，极端程度只能作弱参考。",
            "lens_is_risk_overlay_not_direction": True,
        },
        "main_supporting_factors": ["系统未放行交易许可。"],
        "main_risks_or_conflicts": ["冲突比例仍高。"],
        "operator_focus": ["观察反向证据是否回落。"],
        "invalid_if": ["同向证据失效。"],
        "data_quality_note": "rank 样本仍在 warming up。",
        "not_trading_advice": True,
    }


def transition_model_payload():
    return {
        "transition_summary_cn": "程序化差分显示宏观压力跳升，系统由偏多支持转为中性阻断。",
        "trajectory_state": "DETERIORATING",
        "signal_continuity": "NEUTRALIZED",
        "observed_changes": [
            {
                "domain": "MACRO",
                "effect_target": "GATE_OR_BLOCKING",
                "fact_cn": "",
                "impact_cn": "宏观压力跨过冲击门阈值，由背景扰动升级为主动风险约束，并削弱原有偏多骨架的环境支撑。",
                "tendency_cn": "风险约束/压制",
                "evidence_refs": [
                    "EV_DOMAIN_MACRO",
                    "EV_CORE_MACRO",
                ],
                "evidence_status": "SUFFICIENT",
                "directional_role": "RISK_CONSTRAINT",
                "magnitude_verdict": "changes_judgment",
                "audit_attention_effect": "SHIFT_FOCUS",
                "epistemic_status": "SUPPORTED_INFERENCE",
                "materiality": "CRITICAL",
            }
        ],
        "cross_factor_interactions": [
            "宏观压力与 Funding 拥挤同步变差，构成多域风险趋同。"
        ],
        "cross_factor_assessments": [
            {
                "domains": ["MACRO", "FUNDING"],
                "relation": "REINFORCING",
                "assessment_cn": "宏观压力与资金费率变化共同提高风险约束，需要优先核验是否只是同一冲击下的同步反应。",
                "evidence_refs": [
                    "EV_DOMAIN_MACRO",
                    "EV_DISPLAY_FUNDING_LAST_RATE",
                ],
            },
            {
                "domains": ["TMV", "GAMMA", "SKEW", "P_C_RATIO"],
                "relation": "CONSTRAINT_INTERACTION",
                "assessment_cn": "量价路径、净 Gamma、期权偏斜和 P/C 共同给出骨架背景；稳定项只进入综合论证，不伪造成新增变化。",
                "evidence_refs": [
                    "EV_CORE_TMV",
                    "EV_CORE_GAMMA",
                    "EV_CORE_SKEW",
                    "EV_CORE_P_C_RATIO",
                ],
            }
        ],
        "candidate_explanations": [
            {
                "explanation_cn": "宏观压力与资金费率变化可能是同一上游冲击下的共振，而非已证实因果链。",
                "relation": "CONSISTENT_WITH",
                "supporting_evidence_refs": ["EV_DOMAIN_MACRO", "EV_DISPLAY_FUNDING_LAST_RATE"],
                "alternative_explanations_cn": ["两张卡可能共同响应同一上游冲击。"],
                "causal_status": "UNVERIFIED",
            }
        ],
        "anomaly_assessment": {
            "state": "REGIME_SHIFT",
            "basis_cn": "宏观分数变化超过材料阈值。"
        },
        "operator_focus": ["观察 GGR 是否继续转向放大。"],
        "operator_checks": [
            {
                "focus_cn": "核验宏观压力是否持续高于上一张卡的背景水平。",
                "why_cn": "这决定宏观变化是短暂噪声还是足以改变审计关注重点。",
                "strengthens_if_cn": "若后续卡仍显示宏观压力高位且 Funding 未回落，则风险约束解释增强。",
                "weakens_if_cn": "若宏观压力回落且 Funding 恢复中性，则该变化更可能只是短暂扰动。",
                "evidence_refs": [
                    "EV_DOMAIN_MACRO",
                    "EV_DISPLAY_FUNDING_LAST_RATE",
                ],
            }
        ],
        "invalid_if": ["两张卡不属于可比较市场阶段。"],
        "language_guard": {
            "distinguishes_observation_from_causality": True,
            "no_external_data": True,
            "no_trading_instruction": True,
        },
    }


def transition_reconciliation_payload():
    return {
        "transition_summary_cn": "盲读与系统阻断标签方向一致，但仍需核验宏观压力是否持续。",
        "cross_factor_interactions": ["系统阻断标签比盲读观察更强。"],
        "operator_focus": ["核验宏观压力是否持续。"],
        "operator_checks": [
            {
                "focus_cn": "核验宏观压力是否持续高于上一张卡。",
                "why_cn": "这决定系统阻断标签与盲读证据是否保持一致。",
                "strengthens_if_cn": "若后续卡仍显示宏观压力高位，则张力减弱。",
                "weakens_if_cn": "若宏观压力回落，则系统标签与盲读证据张力增强。",
                "evidence_refs": ["EV_DOMAIN_MACRO"],
            }
        ],
        "invalid_if": ["两张卡不属于可比较市场阶段。"],
        "blind_consistency": "TENSION_WITH_SYSTEM_ASSERTIONS",
        "blind_differences_cn": ["系统阻断标签比盲读观察更强。"],
        "language_guard": {
            "distinguishes_observation_from_causality": True,
            "no_external_data": True,
            "no_trading_instruction": True,
        },
        "not_trading_advice": True,
    }


def test_transition_llm_mode_uses_program_delta_only_and_writes_sidecar():
    tool = load_module(GEMINI_TOOL, "gemini_signal_transition_llm_review")
    transition = transition_record()
    packet = tool.build_transition_review_packet(transition)
    packet_text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert_true("core_skeleton" in packet,
                "transition packet should lead with the multi-domain event skeleton")
    assert_true("domain_change_summaries" in packet,
                "transition packet should include grouped domain summaries")
    assert_true("core_transition_display" in packet,
                "transition packet should include display-ready semantic rows")
    assert_true(packet["schema"]["name"] == "SignalTransitionReviewPacket@1.1.1",
                "transition packet should version evidence-catalog additions")
    assert_true(packet["evidence_catalog_schema_version"] == "transition_evidence_catalog@1.0.0"
                and str(packet["evidence_catalog_hash"]).startswith("sha256:"),
                "transition packet should version and hash the evidence catalog")
    assert_true(packet["evidence_catalog_hash"] == tool.build_transition_review_packet(transition)["evidence_catalog_hash"],
                "transition evidence catalog hash should be stable for the same packet")
    evidence_ids = [item.get("id") for item in packet.get("evidence_catalog", [])]
    assert_true("EV_DOMAIN_MACRO" in evidence_ids
                and "EV_DISPLAY_FUNDING_LAST_RATE" in evidence_ids,
                "transition packet should expose stable evidence IDs")
    assert_true(packet["core_transition_display"][0]["meaning_cn"].startswith("维持中性偏好"),
                "transition display rows should carry Chinese semantic reasoning")
    assert_true(packet["domain_change_summaries"][0]["domain"] == "MACRO",
                "MACRO should be represented once as a grouped domain summary")
    assert_true(len([item for item in packet["domain_change_summaries"]
                     if item.get("domain") == "MACRO"]) == 1,
                "transition packet should not repeat split MACRO component rows as top-level changes")
    assert_true("top_material_changes" in packet_text,
                "transition packet should keep raw material deltas only as trace")
    assert_true("FULL_AUDIT_PACKET" not in packet_text
                and '"factor_cross_section":' not in packet_text,
                "transition packet must not embed full audit card objects")
    assert_true("trade_allowed" not in packet_text and "position" not in packet_text,
                "transition packet should not include execution/account fields")
    prompt = tool.build_transition_review_prompt(packet)
    assert_true("只解释程序已经计算出的 transition delta" in prompt,
                "transition prompt should forbid LLM recalculation")
    assert_true("相关性等于因果" in prompt,
                "transition prompt should guard causal certainty")
    assert_true("不得输出交易建议" in prompt,
                "transition prompt should forbid trading advice")
    assert_true("SignalTransitionReviewPacket" in prompt,
                "transition prompt should name the packet boundary")
    assert_true("EVIDENCE" in prompt and "SYSTEM_ASSERTIONS" in prompt,
                "transition prompt should separate evidence from system assertions")
    assert_true("优先使用 evidence_catalog 中的 evidence_id" in prompt,
                "transition prompt should prefer stable evidence IDs over JSON Pointer")
    assert_true("core_skeleton" in prompt and "domain_change_summaries" in prompt,
                "transition prompt should anchor the LLM to skeleton and grouped summaries")
    assert_true("core_transition_display" in prompt,
                "transition prompt should prioritize display-ready semantic rows")
    assert_true("NEUTRAL 写成“中性”" in prompt
                and "MACRO_BLOCKING 写成“宏观硬阻断”" in prompt
                and "Headwind 写成“逆风”" in prompt,
                "transition prompt should explicitly forbid raw enum wording in Chinese conclusions")
    assert_true("P/C 是非负比率" in prompt and "禁止写“正负符号翻转”" in prompt,
                "transition prompt should block false sign-flip semantics for non-negative ratios")
    assert_true("impact_cn" in prompt
                and "tendency_cn" in prompt
                and "magnitude_verdict" in prompt
                and "operator_checks" in prompt
                and "禁止使用“评估为关键变化”" in prompt,
                "transition prompt should require impact/tendency/operator-check wording and ban materiality boilerplate")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        ledger = root / "signal_transition_ledger.jsonl"
        reviews = root / "signal_transition_llm_reviews.jsonl"
        ledger.write_text(json.dumps(transition, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        calls = []

        def fake_call(api_key, model, request_body, timeout):
            del api_key, model, timeout
            calls.append(request_body)
            schema = request_body["generationConfig"]["responseSchema"]
            assert_true("transition_summary_cn" in schema["required"],
                        "transition schema should require transition summary")
            observed_schema = schema["properties"]["observed_changes"]["items"]
            assert_true("impact_cn" in observed_schema["required"]
                        and "tendency_cn" in observed_schema["required"]
                        and "effect_target" in observed_schema["required"]
                        and "evidence_refs" in observed_schema["required"]
                        and "magnitude_verdict" in observed_schema["required"],
                        "transition observed changes should require v1.1 evidence and reasoning fields")
            assert_true("cross_factor_assessments" in schema["required"]
                        and "candidate_explanations" in schema["required"]
                        and "operator_checks" in schema["required"],
                        "transition schema should require structured assessments, explanations, and checks")
            return {"candidates": [{"content": {"parts": [
                {"text": json.dumps(transition_model_payload(), ensure_ascii=False)}
            ]}}]}

        result = tool.generate_transition_reviews(
            ledger,
            reviews,
            api_key="test-key",
            model="gemini-3.5-flash",
            limit=5,
            call_gemini=fake_call,
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(result["written_reviews"] == 1,
                    "one transition review should be written")
        assert_true(len(calls) == 1,
                    "transition mode should make one focused Gemini call")
        saved = json.loads(reviews.read_text(encoding="utf-8"))
        assert_true(saved["transition_id"] == transition["transition_id"],
                    "sidecar should key by transition id")
        review = saved["transition_llm_review"]
        assert_true(review["schema_version"] == "signal_transition_llm_review@1.2.4",
                    "transition LLM schema version")
        assert_true(review["prompt_version"] == "gemini_signal_transition_review_prompt@1.2.4",
                    "transition prompt version")
        assert_true(tool.build_gemini_request("x")["generationConfig"]["temperature"] == 0.2,
                    "card-level review temperature should remain unchanged")
        assert_true(tool.build_transition_gemini_request("x")["generationConfig"]["temperature"] == 0,
                    "transition review should use deterministic temperature")
        assert_true(tool.build_transition_blind_gemini_request("x")["generationConfig"]["temperature"] == 0,
                    "transition blind experiment should use deterministic temperature")
        assert_true(tool.build_transition_reconciliation_gemini_request("x")["generationConfig"]["temperature"] == 0,
                    "transition reconciliation should use deterministic temperature")
        assert_true(review["evidence_catalog_schema_version"] == "transition_evidence_catalog@1.0.0"
                    and review["evidence_catalog_hash"] == packet["evidence_catalog_hash"],
                    "transition sidecar should preserve evidence catalog provenance")
        assert_true(review["blind_review_mode"] == "single_call_evidence_first",
                    "transition v1.2 should use honest single-call evidence-first mode by default")
        assert_true(review["llm_call_count"] == 1,
                    "transition production path should remain a single call by default")
        assert_true(review["status"] == "OK",
                    "transition review status")
        assert_true(review["input_packet_hash"].startswith("sha256:"),
                    "transition review should be reproducible by packet hash")
        assert_true(review["language_guard"]["no_trading_instruction"] is True,
                    "transition LLM review should preserve no-trading guard")
        assert_true(review["language_guard"]["no_external_data"] is True,
                    "transition LLM review should preserve no-external-data guard")
        assert_true(review["language_guard"]["distinguishes_observation_from_causality"] is True,
                    "transition LLM review should preserve causality language guard")
        assert_true(review["not_trading_advice"] is True,
                    "transition LLM review should be explicitly advisory only")
        observed = review["observed_changes"][0]
        observed_text = " ".join(str(observed.get(key, ""))
                                 for key in ("fact_cn", "impact_cn", "tendency_cn"))
        raw_path_tokens = (
            "factor_cross_section",
            "macro_pressure.components",
            "source_ref",
            "primary_fields",
            "scoring_bps",
        )
        assert_true(not any(token in observed["fact_cn"] for token in raw_path_tokens),
                    "runner-derived transition fact should not expose raw field paths: "
                    + observed["fact_cn"])
        assert_true(observed["fact_cn"].startswith("宏观：")
                    and "宏观压力跨过冲击门阈值" in observed["impact_cn"]
                    and "风险约束" in observed["tendency_cn"],
                    "runner should derive facts while preserving model impact and tendency wording")
        assert_true(observed["effect_target"] == "GATE_OR_BLOCKING",
                    "transition observed changes should preserve effect target")
        assert_true(observed["evidence_status"] == "SUFFICIENT"
                    and observed["directional_role"] == "RISK_CONSTRAINT"
                    and observed["magnitude_verdict"] == "changes_judgment"
                    and observed["audit_attention_effect"] == "SHIFT_FOCUS"
                    and observed["epistemic_status"] == "SUPPORTED_INFERENCE",
                    "transition observed changes should preserve v1.1 audit metadata")
        assert_true(review["cross_factor_assessments"][0]["relation"] == "REINFORCING",
                    "transition review should preserve structured cross-factor assessment")
        assert_true(review["candidate_explanations"][0]["causal_status"] == "UNVERIFIED",
                    "transition explanations should avoid causal certainty")
        assert_true(review["candidate_causal_hypotheses"] == [],
                    "transition v1.2 should not persist model causal hypotheses as active findings")
        assert_true(review["operator_checks"][0]["focus_cn"].startswith("核验宏观压力"),
                    "transition review should preserve structured operator checks")
        assert_true(review["policy_validation"]["passed"] is True,
                    "transition review should pass local policy validation")
        assert_true(review["policy_validation"]["severity"] == "OK"
                    and review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "policy validation should include severity and frontend render state")
        assert_true(review["policy_validation"]["invalid_evidence_refs"] == [],
                    "transition review should not report valid packet evidence refs")
        assert_true("评估为" not in observed_text
                    and "材料性" not in observed_text
                    and "关键变化" not in observed_text,
                    "transition observed changes should not use vague materiality boilerplate")

        enum_payload = transition_model_payload()
        enum_payload["observed_changes"][0]["domain"] = "FUNDING"
        enum_payload["observed_changes"][0]["effect_target"] = "CROWDING_OR_LEVERAGE"
        enum_payload["observed_changes"][0]["impact_cn"] = (
            "资金费率回落并转负，说明永续端拥挤压力缓和，只作为背景约束变化。")
        enum_payload["observed_changes"][0]["tendency_cn"] = "中性/拥挤缓和"
        enum_payload["observed_changes"][0]["evidence_refs"] = ["EV_DISPLAY_FUNDING_LAST_RATE"]
        enum_payload["observed_changes"][0]["directional_role"] = "NEUTRAL_OR_EASING"
        enum_payload["observed_changes"][0]["magnitude_verdict"] = "background_only"
        enum_payload["observed_changes"][0]["audit_attention_effect"] = "BACKGROUND_ONLY"
        enum_payload["signal_continuity"] = "NEUTRALIZED"
        enum_review = tool.build_transition_llm_review(
            transition,
            enum_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        enum_policy = enum_review["policy_validation"]
        assert_true(enum_policy["passed"] is True
                    and enum_policy["raw_enum_leaks"] == []
                    and enum_policy["render_state"] == "DISPLAY_LLM_TEXT",
                    "structured enum values should not trigger raw enum leak suppression: "
                    + json.dumps(enum_policy, ensure_ascii=False, sort_keys=True))

        invalid_ref_payload = transition_model_payload()
        invalid_ref_payload["observed_changes"][0]["evidence_refs"] = ["EV_MISSING"]
        invalid_ref_review = tool.build_transition_llm_review(
            transition,
            invalid_ref_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(invalid_ref_review["policy_validation"]["passed"] is False
                    and invalid_ref_review["policy_validation"]["invalid_evidence_refs"] == ["EV_MISSING"]
                    and invalid_ref_review["policy_validation"]["severity"] == "ERROR",
                    "invalid evidence IDs should be flagged by runner-side policy validation")

        empty_ref_payload = transition_model_payload()
        empty_ref_payload["observed_changes"][0]["evidence_refs"] = []
        empty_ref_review = tool.build_transition_llm_review(
            transition,
            empty_ref_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(empty_ref_review["policy_validation"]["passed"] is False
                    and "missing_evidence_ref" in empty_ref_review["policy_validation"]["issue_codes"]
                    and empty_ref_review["policy_validation"]["severity"] == "ERROR",
                    "sufficient observed changes must cite packet evidence")

        partial_empty_ref_payload = transition_model_payload()
        partial_empty_ref_payload["observed_changes"][0]["fact_cn"] = "模型声称观察到宏观压力变化。"
        partial_empty_ref_payload["observed_changes"][0]["evidence_refs"] = []
        partial_empty_ref_payload["observed_changes"][0]["evidence_status"] = "PARTIAL"
        partial_empty_ref_payload["observed_changes"][0]["magnitude_verdict"] = "background_only"
        partial_empty_ref_payload["observed_changes"][0]["epistemic_status"] = "OBSERVED"
        partial_empty_ref_review = tool.build_transition_llm_review(
            transition,
            partial_empty_ref_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(partial_empty_ref_review["policy_validation"]["passed"] is False
                    and "missing_evidence_ref" in partial_empty_ref_review["policy_validation"]["issue_codes"],
                    "observed changes should cite evidence even for partial/background observations")

        system_pointer_payload = transition_model_payload()
        system_pointer_payload["observed_changes"][0]["evidence_refs"] = ["/decision_transition"]
        system_pointer_review = tool.build_transition_llm_review(
            transition,
            system_pointer_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(system_pointer_review["policy_validation"]["passed"] is False
                    and system_pointer_review["policy_validation"]["invalid_evidence_refs"] == ["/decision_transition"]
                    and "system_assertion_evidence_ref" in system_pointer_review["policy_validation"]["issue_codes"],
                    "system assertion pointers should not satisfy evidence references")

        policy_pointer_payload = transition_model_payload()
        policy_pointer_payload["observed_changes"][0]["evidence_refs"] = ["/evidence_ref_policy"]
        policy_pointer_review = tool.build_transition_llm_review(
            transition,
            policy_pointer_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(policy_pointer_review["policy_validation"]["passed"] is False
                    and policy_pointer_review["policy_validation"]["invalid_evidence_refs"] == ["/evidence_ref_policy"],
                    "evidence policy text should not be accepted as evidence")

        glossary_only_payload = transition_model_payload()
        glossary_only_payload["observed_changes"][0]["evidence_refs"] = ["EV_FIELD_GLOSSARY"]
        glossary_only_review = tool.build_transition_llm_review(
            transition,
            glossary_only_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(glossary_only_review["policy_validation"]["passed"] is False
                    and "missing_evidence_ref" in glossary_only_review["policy_validation"]["issue_codes"],
                    "glossary-only refs should remain traceable but not satisfy observed-change evidence")

        decision_payload = transition_model_payload()
        decision_payload["observed_changes"][0]["domain"] = "DECISION"
        decision_payload["observed_changes"][0]["effect_target"] = "DIRECTIONAL_SKELETON"
        decision_payload["observed_changes"][0]["evidence_refs"] = ["EV_DISPLAY_DECISION_CONFIDENCE"]
        decision_payload["observed_changes"][0]["impact_cn"] = "系统决策置信下降，因此方向骨架变弱。"
        decision_review = tool.build_transition_llm_review(
            transition,
            decision_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(decision_review["policy_validation"]["passed"] is False
                    and "system_assertion_observed_change" in decision_review["policy_validation"]["issue_codes"],
                    "decision/system assertion rows should not become independent observed changes")

        gamma_target_payload = transition_model_payload()
        gamma_target_payload["observed_changes"][0]["domain"] = "GAMMA"
        gamma_target_payload["observed_changes"][0]["effect_target"] = "DIRECTIONAL_SKELETON"
        gamma_target_payload["observed_changes"][0]["evidence_refs"] = ["EV_CORE_GAMMA"]
        gamma_target_payload["observed_changes"][0]["impact_cn"] = (
            "Gamma 变化直接削弱方向骨架。")
        gamma_target_review = tool.build_transition_llm_review(
            transition,
            gamma_target_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(gamma_target_review["policy_validation"]["passed"] is False
                    and "invalid_effect_target_for_domain" in gamma_target_review["policy_validation"]["issue_codes"],
                    "Gamma/GEX findings should not directly target directional skeleton")

        direction_conflict_payload = transition_model_payload()
        direction_conflict_payload["observed_changes"][0]["impact_cn"] = (
            "宏观压力明显回落，风险约束减弱并对当前方向骨架形成支撑。")
        direction_conflict_payload["observed_changes"][0]["tendency_cn"] = "支撑/缓和"
        direction_conflict_payload["observed_changes"][0]["directional_role"] = "NEUTRAL_OR_EASING"
        direction_conflict_review = tool.build_transition_llm_review(
            transition,
            direction_conflict_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(direction_conflict_review["policy_validation"]["passed"] is True
                    and "fact_impact_direction_conflict" in direction_conflict_review["policy_validation"]["issue_codes"]
                    and direction_conflict_review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "content-expression conflicts should be recorded but not gate LLM display")

        understated_payload = transition_model_payload()
        understated_payload["observed_changes"][0]["directional_role"] = "UNDETERMINED"
        understated_payload["observed_changes"][0]["magnitude_verdict"] = "indeterminate"
        understated_payload["observed_changes"][0]["audit_attention_effect"] = "UNDETERMINED"
        understated_review = tool.build_transition_llm_review(
            transition,
            understated_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(understated_review["policy_validation"]["passed"] is False
                    and "sufficient_evidence_understated" in understated_review["policy_validation"]["issue_codes"],
                    "sufficient evidence should not be hidden behind fully indeterminate wording")

        empty_findings_payload = transition_model_payload()
        empty_findings_payload["observed_changes"] = []
        empty_findings_review = tool.build_transition_llm_review(
            transition,
            empty_findings_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(empty_findings_review["policy_validation"]["passed"] is False
                    and "missing_observed_changes" in empty_findings_review["policy_validation"]["issue_codes"]
                    and empty_findings_review["policy_validation"]["render_state"] == "DEGRADED_LLM_TEXT",
                    "reviewable transition output must contain at least one observed change")

        trading_payload = transition_model_payload()
        trading_payload["operator_checks"][0]["focus_cn"] = "如果确认就开仓"
        trading_review = tool.build_transition_llm_review(
            transition,
            trading_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(trading_review["policy_validation"]["passed"] is True
                    and "开仓" in trading_review["policy_validation"]["trading_instruction_terms"]
                    and trading_review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "trading-language content should be metadata only for transition LLM display")

        not_comparable_payload = transition_model_payload()
        not_comparable_payload["observed_changes"][0]["evidence_status"] = "NOT_COMPARABLE"
        not_comparable_payload["observed_changes"][0]["magnitude_verdict"] = "changes_judgment"
        not_comparable_review = tool.build_transition_llm_review(
            transition,
            not_comparable_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(not_comparable_review["policy_validation"]["passed"] is False
                    and "incompatible_epistemic_state" in not_comparable_review["policy_validation"]["issue_codes"],
                    "not-comparable evidence should not be allowed to claim judgment-changing magnitude")

        pcr_missing_payload = transition_model_payload()
        pcr_missing_payload["observed_changes"][0]["domain"] = "P_C_RATIO"
        pcr_missing_payload["observed_changes"][0]["effect_target"] = "DATA_RELIABILITY"
        pcr_missing_payload["observed_changes"][0]["fact_cn"] = "期权认沽认购比率数据持续缺失。"
        pcr_missing_payload["observed_changes"][0]["impact_cn"] = (
            "期权保护需求与相对期权需求变化无法评估，导致该维度只能作为数据可靠性提示。")
        pcr_missing_payload["observed_changes"][0]["tendency_cn"] = "中性/不可评估"
        pcr_missing_payload["observed_changes"][0]["evidence_refs"] = ["EV_CORE_P_C_RATIO"]
        pcr_missing_payload["observed_changes"][0]["evidence_status"] = "MISSING"
        pcr_missing_payload["observed_changes"][0]["directional_role"] = "UNDETERMINED"
        pcr_missing_payload["observed_changes"][0]["magnitude_verdict"] = "indeterminate"
        pcr_missing_payload["observed_changes"][0]["audit_attention_effect"] = "BACKGROUND_ONLY"
        pcr_missing_payload["observed_changes"][0]["epistemic_status"] = "NOT_ASSESSABLE"
        pcr_missing_review = tool.build_transition_llm_review(
            transition,
            pcr_missing_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true("invalid_effect_target_for_domain" not in pcr_missing_review["policy_validation"]["issue_codes"],
                    "missing P/C data may target DATA_RELIABILITY without domain-target failure")

        leverage_context_payload = transition_model_payload()
        leverage_context_payload["cross_factor_assessments"][0]["assessment_cn"] = (
            "资金费率提示杠杆拥挤度保持稳定，只作为拥挤背景观察，不构成执行建议。")
        leverage_context_review = tool.build_transition_llm_review(
            transition,
            leverage_context_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true("trading_instruction" not in leverage_context_review["policy_validation"]["issue_codes"],
                    "crowding/leverage context should not be treated as a trading instruction")

        raw_path_payload = transition_model_payload()
        raw_path_payload["transition_summary_cn"] = (
            "宏观 原始变化 6 项，主要字段：macro_pressure.components.US10Y.scoring_bps，"
            "来源：factor_cross_section.macro_pressure；核心前后值已入包。")
        raw_path_review = tool.build_transition_llm_review(
            transition,
            raw_path_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(raw_path_review["policy_validation"]["passed"] is True
                    and "raw_field_path_leak" in raw_path_review["policy_validation"]["issue_codes"]
                    and raw_path_review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "raw field/path leakage should be recorded but not gate transition LLM display")

        external_payload = transition_model_payload()
        external_payload["candidate_explanations"][0]["explanation_cn"] = (
            "外部宏观环境变化、外部宏观事件以及地缘政治风险升温可能导致市场风险偏好变化。")
        external_review = tool.build_transition_llm_review(
            transition,
            external_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(external_review["policy_validation"]["passed"] is True
                    and "external_data_claim" in external_review["policy_validation"]["issue_codes"]
                    and external_review["policy_validation"]["no_external_data"] is False,
                    "packet-external macro/news explanations should be metadata only")

        soft_external_payload = transition_model_payload()
        soft_external_payload["candidate_explanations"][0]["explanation_cn"] = (
            "宏观美债收益率与美元指数压力上升，可能与宏观流动性收紧共振，"
            "导致宏观冲击门阻断激活，需人工核对包内数据是否同源。")
        soft_external_review = tool.build_transition_llm_review(
            transition,
            soft_external_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(soft_external_review["policy_validation"]["passed"] is True
                    and "external_data_claim" in soft_external_review["policy_validation"]["issue_codes"]
                    and "causal_overclaim" in soft_external_review["policy_validation"]["issue_codes"],
                    "external and causal expression issues should not gate LLM display")

        funding_flow_payload = transition_model_payload()
        funding_flow_payload["candidate_explanations"][0]["alternative_explanations_cn"] = [
            "不同的短期资金流向导致，需人工核对。"
        ]
        funding_flow_review = tool.build_transition_llm_review(
            transition,
            funding_flow_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(funding_flow_review["policy_validation"]["passed"] is True
                    and "external_data_claim" in funding_flow_review["policy_validation"]["issue_codes"],
                    "external short-term fund-flow explanations should be metadata only")

        trigger_overclaim_payload = transition_model_payload()
        trigger_overclaim_payload["transition_summary_cn"] = (
            "美债收益率与美元指数评分上升，触发宏观冲击门阻断。")
        trigger_overclaim_review = tool.build_transition_llm_review(
            transition,
            trigger_overclaim_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(trigger_overclaim_review["policy_validation"]["passed"] is True
                    and "causal_overclaim" in trigger_overclaim_review["policy_validation"]["issue_codes"],
                    "deterministic trigger wording should be metadata only")

        quality_state_payload = transition_model_payload()
        quality_state_payload["anomaly_assessment"]["basis_cn"] = (
            "P/C 比率数据缺失，但整体数据质量评估为正常。")
        quality_state_review = tool.build_transition_llm_review(
            transition,
            quality_state_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true("materiality_boilerplate" not in quality_state_review["policy_validation"]["issue_codes"],
                    "ordinary quality-state wording should not be mistaken for materiality boilerplate")

        skew_coherence_payload = transition_model_payload()
        skew_coherence_payload["observed_changes"][0]["domain"] = "SKEW"
        skew_coherence_payload["observed_changes"][0]["effect_target"] = "SIGNAL_COHERENCE"
        skew_coherence_payload["observed_changes"][0]["fact_cn"] = "期权偏斜保持稳定。"
        skew_coherence_payload["observed_changes"][0]["impact_cn"] = "偏斜稳定用于解释信号一致性背景。"
        skew_coherence_payload["observed_changes"][0]["tendency_cn"] = "中性/缓和"
        skew_coherence_payload["observed_changes"][0]["evidence_refs"] = ["EV_CORE_SKEW"]
        skew_coherence_review = tool.build_transition_llm_review(
            transition,
            skew_coherence_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true("invalid_effect_target_for_domain" not in skew_coherence_review["policy_validation"]["issue_codes"],
                    "SKEW should be allowed to explain signal coherence when used as background")

        funding_units_payload = transition_model_payload()
        funding_units_payload["observed_changes"][0].update({
            "domain": "FUNDING",
            "effect_target": "CROWDING_OR_LEVERAGE",
            "fact_cn": "资金费率从 2.999e-05 升至 7.117e-05。",
            "impact_cn": "资金费率上升但仍低于阈值，只说明永续端温和多头倾向，不构成拥挤升温。",
            "tendency_cn": "温和多头倾向",
            "evidence_refs": ["EV_DISPLAY_FUNDING_LAST_RATE"],
            "directional_role": "NEUTRAL_OR_EASING",
            "magnitude_verdict": "background_only",
            "audit_attention_effect": "BACKGROUND_ONLY",
            "epistemic_status": "OBSERVED",
        })
        transition["core_transition_display"] = [
            row if row.get("domain") != "FUNDING" else {
                **row,
                "previous_display": "0.003%",
                "current_display": "0.0071%",
                "delta_display": "0.0041%",
                "meaning_cn": "资金费率低于 0.01% 阈值，当前为温和多头倾向，不代表拥挤升温。",
            }
            for row in transition["core_transition_display"]
        ]
        funding_units_review = tool.build_transition_llm_review(
            transition,
            funding_units_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        funding_change = funding_units_review["observed_changes"][0]
        funding_text = " ".join(str(funding_change.get(key, ""))
                                for key in ("fact_cn", "impact_cn", "tendency_cn"))
        assert_true("e-05" not in funding_text and "0.0071%" in funding_text,
                    "Funding human text should use display percent values, not scientific notation: "
                    + funding_text)
        assert_true("拥挤升温" not in funding_text and "温和多头倾向" in funding_text,
                    "Funding below threshold should stay mild-long, not crowding escalation: "
                    + funding_text)
        assert_true("scientific_notation_in_human_text" in funding_units_review["policy_validation"]["issue_codes"]
                    and "numeric_display_mismatch" in funding_units_review["policy_validation"]["issue_codes"],
                    "policy should record that the raw model text was normalized")

        gamma_units_payload = transition_model_payload()
        gamma_units_payload["observed_changes"][0].update({
            "domain": "GAMMA",
            "effect_target": "VOLATILITY_SPACE",
            "fact_cn": "净Gamma负值从 -0.151 USD 收窄至 -0.042 USD。",
            "impact_cn": "Gamma 只解释波动空间与钉住约束，不直接形成方向信号。",
            "tendency_cn": "空间约束",
            "evidence_refs": ["EV_DISPLAY_GAMMA_NET_GAMMA_NOTIONAL_USD"],
            "directional_role": "MIXED",
            "magnitude_verdict": "changes_judgment",
            "audit_attention_effect": "SHIFT_FOCUS",
            "epistemic_status": "OBSERVED",
        })
        if not any(row.get("domain") == "GAMMA" for row in transition["core_transition_display"]):
            transition["core_transition_display"].append({
                "domain": "GAMMA",
                "title_cn": "Gamma（净 Gamma）",
                "value_key": "net_gamma_notional_usd",
                "previous_display": "-$152M",
                "current_display": "-$42M",
                "delta_display": "$110M",
                "source_note": "净 Gamma USD 名义额",
                "meaning_cn": "净 Gamma 仍为负值，用于解释波动放大和空间约束，不是方向信号。",
            })
        transition["core_transition_display"] = [
            row if row.get("domain") != "GAMMA" else {
                **row,
                "previous_display": "-$152M",
                "current_display": "-$42M",
                "delta_display": "$110M",
                "source_note": "净 Gamma USD 名义额",
                "meaning_cn": "净 Gamma 仍为负值，用于解释波动放大和空间约束，不是方向信号。",
            }
            for row in transition["core_transition_display"]
        ]
        gamma_units_review = tool.build_transition_llm_review(
            transition,
            gamma_units_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        gamma_change = gamma_units_review["observed_changes"][0]
        gamma_text = " ".join(str(gamma_change.get(key, ""))
                              for key in ("fact_cn", "impact_cn", "tendency_cn"))
        assert_true("-0.151 USD" not in gamma_text and "-$152M" in gamma_text,
                    "Gamma human text should align with display USD notional: " + gamma_text)
        assert_true("gamma_usd_unit_misread" in gamma_units_review["policy_validation"]["issue_codes"]
                    and "numeric_display_mismatch" in gamma_units_review["policy_validation"]["issue_codes"],
                    "policy should record suspicious small-USD Gamma normalization")

        for gamma_alias in ("GEX", "GAMMA_GEX"):
            gamma_alias_payload = json.loads(json.dumps(gamma_units_payload, ensure_ascii=False))
            gamma_alias_payload["observed_changes"][0]["domain"] = gamma_alias
            gamma_alias_review = tool.build_transition_llm_review(
                transition,
                gamma_alias_payload,
                model="gemini-3.5-flash",
                reviewed_at="2026-06-19T00:00:00+00:00",
            )
            gamma_alias_change = gamma_alias_review["observed_changes"][0]
            gamma_alias_text = " ".join(str(gamma_alias_change.get(key, ""))
                                        for key in ("fact_cn", "impact_cn", "tendency_cn"))
            assert_true("-0.151 USD" not in gamma_alias_text and "-$152M" in gamma_alias_text,
                        gamma_alias + " should share Gamma/GEX USD display normalization: "
                        + gamma_alias_text)
            assert_true("gamma_usd_unit_misread" in gamma_alias_review["policy_validation"]["issue_codes"]
                        and "numeric_display_mismatch" in gamma_alias_review["policy_validation"]["issue_codes"],
                        gamma_alias + " should record Gamma/GEX unit normalization")

        internal_macro_payload = transition_model_payload()
        internal_macro_payload["candidate_explanations"][0]["explanation_cn"] = (
            "包内宏观背景显示美债收益率、美元指数与波动率压力共同抬升，"
            "只能解释为宏观压力同步变化，不能写成外部事件原因。")
        internal_macro_review = tool.build_transition_llm_review(
            transition,
            internal_macro_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true("external_data_claim" not in internal_macro_review["policy_validation"]["issue_codes"],
                    "packet-internal macro fields should not be treated as external data claims")

        hidden_ref_coverage_payload = transition_model_payload()
        hidden_ref_coverage_payload["transition_summary_cn"] = "只说明宏观压力上升，未给出其他核心骨架论证。"
        hidden_ref_coverage_payload["observed_changes"] = [
            {
                "domain": "MACRO",
                "fact_cn": "宏观压力上升。",
                "impact_cn": "宏观压力进入阻断状态。",
                "tendency_cn": "风险约束",
                "evidence_refs": [
                    "EV_DOMAIN_MACRO",
                    "EV_CORE_TMV",
                    "EV_CORE_FUNDING",
                    "EV_CORE_SKEW",
                    "EV_CORE_GAMMA",
                    "EV_CORE_P_C_RATIO",
                ],
                "evidence_status": "SUFFICIENT",
                "directional_role": "RISK_CONSTRAINT",
                "magnitude_verdict": "changes_judgment",
                "audit_attention_effect": "SHIFT_FOCUS",
                "epistemic_status": "OBSERVED",
                "effect_target": "GATE_OR_BLOCKING",
            }
        ]
        hidden_ref_coverage_payload["cross_factor_assessments"] = []
        hidden_ref_coverage_review = tool.build_transition_llm_review(
            transition,
            hidden_ref_coverage_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(hidden_ref_coverage_review["policy_validation"]["passed"] is False
                    and "missing_core_domain_coverage" in hidden_ref_coverage_review["policy_validation"]["issue_codes"]
                    and {"TMV", "FUNDING", "GAMMA", "SKEW", "P_C_RATIO"}.issubset(
                        set(hidden_ref_coverage_review["policy_validation"]["missing_core_domain_coverage"])),
                    "core coverage should come from visible domains or summary text, not hidden evidence_refs")

        sparse_payload = transition_model_payload()
        sparse_payload["cross_factor_assessments"] = [
            {
                "domains": ["MACRO"],
                "relation": "CONSTRAINT_INTERACTION",
                "assessment_cn": "只说明宏观压力，缺少量价、资金费率、期权偏斜、Gamma 与 P/C 的综合骨架。",
                "evidence_refs": ["EV_DOMAIN_MACRO"],
            }
        ]
        sparse_review = tool.build_transition_llm_review(
            transition,
            sparse_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(sparse_review["policy_validation"]["passed"] is False
                    and "missing_core_domain_coverage" in sparse_review["policy_validation"]["issue_codes"]
                    and {"TMV", "FUNDING", "GAMMA", "SKEW", "P_C_RATIO"}.issubset(
                        set(sparse_review["policy_validation"]["missing_core_domain_coverage"])),
                    "transition review should cover the complete core market skeleton")

        bad_payload = transition_model_payload()
        bad_payload["language_guard"]["no_external_data"] = False
        bad_guard_review = tool.build_transition_llm_review(
            transition,
            bad_payload,
            model="gemini-3.5-flash",
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(bad_guard_review["policy_validation"]["passed"] is True
                    and bad_guard_review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "language guard self-report should not gate transition LLM display")

        result2 = tool.generate_transition_reviews(
            ledger,
            reviews,
            api_key="test-key",
            model="gemini-3.5-flash",
            limit=5,
            call_gemini=fake_call,
            reviewed_at="2026-06-19T00:00:00+00:00",
        )
        assert_true(result2["skipped_transitions"] == 1,
                    "same transition_id should be skipped after sidecar is written")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        ledger = root / "signal_transition_ledger.jsonl"
        reviews = root / "signal_transition_llm_reviews.jsonl"
        ledger.write_text(json.dumps(transition, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        two_call_requests = []

        def fake_two_call(api_key, model, request_body, timeout):
            del api_key, model, timeout
            text = request_body["contents"][0]["parts"][0]["text"]
            two_call_requests.append(text)
            if "TRANSITION_BLIND_DELTA_PACKET" in text:
                return {"candidates": [{"content": {"parts": [
                    {"text": json.dumps(transition_model_payload(), ensure_ascii=False)}
                ]}}]}
            assert_true("TRANSITION_RECONCILIATION_PACKET" in text,
                        "second transition call should be reconciliation-only")
            schema = request_body["generationConfig"]["responseSchema"]
            assert_true("observed_changes" not in schema["properties"],
                        "second transition response schema should not allow observed_changes")
            return {"candidates": [{"content": {"parts": [
                {"text": json.dumps(transition_reconciliation_payload(), ensure_ascii=False)}
            ]}}]}

        result = tool.generate_transition_reviews(
            ledger,
            reviews,
            api_key="test-key",
            model="gemini-3.5-flash",
            limit=5,
            call_gemini=fake_two_call,
            reviewed_at="2026-06-19T00:00:00+00:00",
            transition_blind_mode="two_call_strict",
        )
        assert_true(result["written_reviews"] == 1,
                    "experimental two-call transition review should write one sidecar")
        assert_true(len(two_call_requests) == 2,
                    "experimental transition blind mode should make two Gemini calls")
        saved = json.loads(reviews.read_text(encoding="utf-8"))
        review = saved["transition_llm_review"]
        assert_true(review["blind_review_mode"] == "transition_two_call_strict"
                    and review["llm_call_count"] == 2,
                    "experimental transition sidecar should record strict two-call mode")
        assert_true(review["blind_packet_hash"].startswith("sha256:")
                    and review["blind_result_hash"].startswith("sha256:"),
                    "experimental two-call mode should preserve blind packet/result hashes")
        assert_true(review["blind_consistency"] == "TENSION_WITH_SYSTEM_ASSERTIONS",
                    "second call should only reconcile blind/system tension")
        assert_true(review["observed_changes"][0]["impact_cn"] == transition_model_payload()["observed_changes"][0]["impact_cn"],
                    "Call 2 must not rewrite Call 1 observed changes")

        injected_reconciliation = transition_reconciliation_payload()
        injected_reconciliation["candidate_explanations"] = transition_model_payload()["candidate_explanations"]
        injected_reconciliation["cross_factor_assessments"] = transition_model_payload()["cross_factor_assessments"]
        injected_reconciliation["anomaly_assessment"] = transition_model_payload()["anomaly_assessment"]
        try:
            tool._validate_transition_reconciliation_payload(injected_reconciliation)
            raise AssertionError("Call 2 reconciliation should reject new finding fields")
        except ValueError as exc:
            assert_true("unexpected fields" in str(exc),
                        "Call 2 rejection should explain unexpected fields")


def test_transition_sort_keys_accept_mixed_timestamp_types():
    tool = load_module(GEMINI_TOOL, "gemini_signal_transition_sort_keys")
    cards = [
        {"identity": {"card_id": "CARD-ISO", "confirmed_at": "2026-06-19T00:00:00+00:00"}},
        {"identity": {"card_id": "CARD-MS", "confirmed_time_ms": 1781827200001}},
        {"identity": {"card_id": "CARD-MISSING"}},
        {"identity": {"card_id": 42, "confirmed_time_ms": 1781827200001}},
    ]
    transitions = [
        {"transition_id": "TR-ISO", "current_confirmed_at": "2026-06-19T00:00:00+00:00"},
        {"transition_id": "TR-MS", "current_ts_ms": 1781827200001},
        {"transition_id": 2, "current_ts_ms": 1781827200001},
        {"transition_id": "TR-MISSING"},
    ]
    assert_true([tool._card_id(card) for card in sorted(cards, key=tool._card_sort_key)]
                == ["CARD-MISSING", "CARD-ISO", "42", "CARD-MS"],
                "card sort should normalize mixed timestamp types")
    assert_true([item["transition_id"] for item in sorted(transitions, key=tool._transition_sort_key)]
                == ["TR-MISSING", "TR-ISO", 2, "TR-MS"],
                "transition sort should normalize mixed timestamp types")


def test_gemini_packet_prompt_and_sidecar_generation():
    tool = load_module(GEMINI_TOOL, "gemini_signal_llm_review")
    assert_true(tool.DEFAULT_MODEL == "gemini-3.5-flash",
                "default Gemini model should be Gemini 3.5 Flash")
    sample = card()
    packet = tool.build_review_packet(sample)
    packet_text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert_true("api_token" not in packet_text, "token key should be removed")
    assert_true("delivery" not in packet_text, "delivery paths should be removed")
    assert_true("should" not in packet_text, "local path should be redacted")
    blind = tool.build_blind_theoretical_packet(packet)
    blind_text = json.dumps(blind, ensure_ascii=False, sort_keys=True)
    for forbidden in ("decision", "reasoning", "conflict", "blocking", "trade_allowed"):
        assert_true(forbidden not in blind_text,
                    "blind theoretical packet should omit " + forbidden)
    assert_true("gex_info" in blind_text and "gamma_regime" in blind_text,
                "blind theoretical packet should retain Gamma/GEX context")
    assert_true(blind["schema"]["derived_blind"] is True,
                "blind packet should declare true two-call blind mode")

    blind_prompt = tool.build_blind_prompt(packet)
    assert_true("FULL_AUDIT_PACKET" not in blind_prompt,
                "blind prompt must not include full audit packet")
    assert_true("BLIND_THEORETICAL_PACKET" in blind_prompt,
                "blind prompt should present blind packet")
    prompt = tool.build_prompt(packet, {
        "theoretical_active_view": model_payload()["theoretical_active_view"],
        "gamma_regime_lens": model_payload()["gamma_regime_lens"],
    })
    assert_true("BLIND_REVIEW_RESULT" in prompt,
                "full prompt should include the first-call blind result")
    assert_true("第一次盲读" in prompt,
                "full prompt should preserve blind audit boundary")
    assert_true("FULL_AUDIT_PACKET" in prompt,
        "prompt should present full audit packet after blind section")
    assert_true("正 Gamma" in blind_prompt and "负 Gamma" in blind_prompt,
                "blind prompt should include Gamma regime lens theory")
    assert_true("不是胜率" in prompt, "prompt should reject confidence-as-win-rate")
    assert_true("不得重算模型" in prompt, "prompt should reject recomputation")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        source.write_text(json.dumps(sample, ensure_ascii=False) + "\n",
                          encoding="utf-8")

        calls = []

        def fake_call(api_key, model, request_body, timeout):
            assert_true(api_key == "test-key", "api key should only be passed to call")
            calls.append(request_body)
            generation = request_body["generationConfig"]
            assert_true(generation["responseMimeType"] == "application/json",
                        "Gemini request should ask for JSON")
            schema = generation["responseSchema"]
            if len(calls) == 1:
                assert_true("summary_cn" not in schema["required"],
                            "blind call should not ask for final summary")
                return {"candidates": [{"content": {"parts": [
                    {"text": json.dumps({
                        "theoretical_active_view": model_payload()["theoretical_active_view"],
                        "gamma_regime_lens": model_payload()["gamma_regime_lens"],
                    }, ensure_ascii=False)}
                ]}}]}
            assert_true("summary_cn" in schema["required"], "schema required summary")
            return {"candidates": [{"content": {"parts": [
                {"text": json.dumps(model_payload(), ensure_ascii=False)}
            ]}}]}

        result = tool.generate_reviews(source, reviews, api_key="test-key",
                                       model="gemini-3.5-flash",
                                       call_gemini=fake_call,
                                       reviewed_at="2026-06-19T00:00:00+00:00")
        assert_true(result["written_reviews"] == 1, "one review written")
        assert_true(len(calls) == 2, "true blind review should make two Gemini calls")
        saved = json.loads(reviews.read_text(encoding="utf-8"))
        review = saved["llm_review"]
        assert_true(review["provider"] == "gemini", "provider should be gemini")
        assert_true(review["blind_review_mode"] == "two_call_strict",
                    "review should record strict two-call blind mode")
        assert_true(review["llm_call_count"] == 2,
                    "review should record two LLM calls")
        assert_true(review["api_key_route"] == "unknown",
                    "fake call without route metadata should not claim a channel")
        active_view = review.get("theoretical_active_view")
        assert_true(isinstance(active_view, dict), "review should include theoretical active view")
        assert_true(active_view["derived_blind"] is True,
                    "two-call theoretical view should mark true blind derivation")
        assert_true(active_view["validation_status"] == "UNVALIDATED",
                    "theoretical view should remain unvalidated by default")
        assert_true(active_view["bias"] == "MIXED_UNCLEAR",
                    "theoretical active view should preserve model bias enum")
        assert_true("不改变系统信号" in active_view["boundary_cn"],
                    "theoretical active view should stay audit-only")
        gamma_lens = review.get("gamma_regime_lens")
        assert_true(isinstance(gamma_lens, dict), "review should include gamma regime lens")
        assert_true(gamma_lens["lens_is_risk_overlay_not_direction"] is True,
                    "gamma lens should be risk overlay, not direction")
        assert_true(gamma_lens["regime"] == "LONG_GAMMA_STABILIZING",
                    "gamma lens should preserve regime enum")
        assert_true(review["caution_level"] == "MEDIUM",
                    "material conflict and warming rank should lift caution floor")


def test_call_gemini_falls_back_to_channel2_only_for_retryable_errors():
    tool = load_module(GEMINI_TOOL, "gemini_signal_llm_review_fallback")
    original_post = tool._post_gemini
    calls = []

    def retryable_first(api_key, model, request_body, timeout):
        del model, request_body, timeout
        calls.append(api_key)
        if api_key == "free-key":
            raise tool.GeminiApiError(503, "high demand")
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    try:
        tool._post_gemini = retryable_first
        response = tool.call_gemini(
            "free-key", "gemini-3.5-flash", {}, timeout=1,
            fallback_api_key="paid-key")
        assert_true(calls == ["free-key", "paid-key"],
                    "503 should retry channel2 after channel1")
        assert_true(response["_api_key_route"] == "channel2",
                    "successful fallback response should record channel2")
    finally:
        tool._post_gemini = original_post

    for status_code in (400, 409, 425):
        calls = []

        def non_retryable_first(api_key, model, request_body, timeout, code=status_code):
            del model, request_body, timeout
            calls.append(api_key)
            raise tool.GeminiApiError(code, "not a capacity failure")

        try:
            tool._post_gemini = non_retryable_first
            try:
                tool.call_gemini(
                    "free-key", "gemini-3.5-flash", {}, timeout=1,
                    fallback_api_key="paid-key")
                raise AssertionError(str(status_code) + " should not fall back to channel2")
            except tool.GeminiApiError as exc:
                assert_true(exc.status_code == status_code,
                            "should keep original " + str(status_code))
                assert_true(exc.api_key_routes == ["channel1"],
                            "error should record attempted channel1 only")
            assert_true(calls == ["free-key"],
                        "non-retryable errors should not spend channel2")
        finally:
            tool._post_gemini = original_post

    calls = []

    def timeout_first(api_key, model, request_body, timeout):
        del model, request_body, timeout
        calls.append(api_key)
        if api_key == "free-key":
            raise TimeoutError("timed out")
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    try:
        tool._post_gemini = timeout_first
        response = tool.call_gemini(
            "free-key", "gemini-3.5-flash", {}, timeout=1,
            fallback_api_key="paid-key")
        assert_true(calls == ["free-key", "paid-key"],
                    "timeout should retry channel2")
        assert_true(response["_api_key_route"] == "channel2",
                    "timeout fallback response should record channel2")
    finally:
        tool._post_gemini = original_post

    calls = []

    def plain_url_error(api_key, model, request_body, timeout):
        del model, request_body, timeout
        calls.append(api_key)
        raise urllib.error.URLError("dns failure")

    try:
        tool._post_gemini = plain_url_error
        try:
            tool.call_gemini(
                "free-key", "gemini-3.5-flash", {}, timeout=1,
                fallback_api_key="paid-key")
            raise AssertionError("plain URLError should not fall back to channel2")
        except urllib.error.URLError as exc:
            assert_true(exc.api_key_routes == ["channel1"],
                        "URL error should record attempted channel1 only")
        assert_true(calls == ["free-key"],
                    "plain URL errors should not spend channel2")
    finally:
        tool._post_gemini = original_post

    calls = []

    def url_timeout(api_key, model, request_body, timeout):
        del model, request_body, timeout
        calls.append(api_key)
        if api_key == "free-key":
            raise urllib.error.URLError(socket.timeout("timed out"))
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    try:
        tool._post_gemini = url_timeout
        response = tool.call_gemini(
            "free-key", "gemini-3.5-flash", {}, timeout=1,
            fallback_api_key="paid-key")
        assert_true(calls == ["free-key", "paid-key"],
                    "URL timeout should retry channel2")
        assert_true(response["_api_key_route"] == "channel2",
                    "URL timeout fallback response should record channel2")
    finally:
        tool._post_gemini = original_post


def test_materializer_merges_sidecar_without_downgrading_inline_ok():
    materializer = load_module(MATERIALIZER_TOOL, "materialize_signal_cards")
    inline = card("CARD-A")
    inline["llm_review"] = {
        "status": "OK",
        "summary_cn": "inline ok",
        "not_trading_advice": True,
    }
    base = card("CARD-B")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        output = root / "public"
        source.write_text(
            json.dumps(inline, ensure_ascii=False) + "\n"
            + json.dumps(base, ensure_ascii=False) + "\n",
            encoding="utf-8")
        reviews.write_text(
            json.dumps({
                "card_id": "CARD-A",
                "llm_review": {
                    "status": "ERROR",
                    "summary_cn": "sidecar error",
                    "not_trading_advice": True,
                },
            }, ensure_ascii=False) + "\n"
            + json.dumps({
                "card_id": "CARD-B",
                "llm_review": {
                    "status": "OK",
                    "summary_cn": "sidecar ok",
                    "not_trading_advice": True,
                },
            }, ensure_ascii=False) + "\n",
            encoding="utf-8")
        result = materializer.materialize(source, output, llm_reviews=reviews)
        assert_true(result["merged_review_count"] == 1,
                    "only base card should receive sidecar review")
        card_a = json.loads((output / "signal_cards" / "CARD-A.json").read_text(encoding="utf-8"))
        card_b = json.loads((output / "signal_cards" / "CARD-B.json").read_text(encoding="utf-8"))
        assert_true(card_a["llm_review"]["summary_cn"] == "inline ok",
                    "sidecar ERROR should not overwrite inline OK")
        assert_true(card_b["llm_review"]["summary_cn"] == "sidecar ok",
                    "sidecar OK should attach to base card")


def test_generate_reviews_redacts_sensitive_error_text():
    tool = load_module(GEMINI_TOOL, "gemini_signal_llm_review_error_redaction")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        source = tmp / "signal_review.jsonl"
        reviews = tmp / "signal_llm_reviews.jsonl"
        source.write_text(json.dumps(card(), ensure_ascii=False) + "\n",
                          encoding="utf-8")

        def failing_call(api_key, model, request_body, timeout):
            del api_key, model, request_body, timeout
            exc = RuntimeError(
                "request failed with AIza" + "A" * 28
                + " AQ." + "C" * 28
                + " GEMINI_API_KEY GEMINI_CHANNEL1_API_KEY GEMINI_CHANNEL2_API_KEY x-goog-api-key Bearer test-token")
            exc.api_key_routes = ["channel1"]
            raise exc

        result = tool.generate_reviews(
            source,
            reviews,
            api_key="AIza" + "B" * 28,
            model="gemini-3.5-flash",
            limit=1,
            include_synthetic=True,
            call_gemini=failing_call,
        )
        assert_true(result["errors"] == 1, "failing fake call should record one error")
        text = reviews.read_text(encoding="utf-8")
        saved = json.loads(text)
        review = saved["llm_review"]
        assert_true(review["api_key_route"] == "channel1",
                    "error sidecar should record attempted channel route")
        assert_true(review["llm_call_routes"] == ["channel1"],
                    "error sidecar should record attempted route list")
        for forbidden in (
                "AIza", "AQ.", "GEMINI_API_KEY", "GEMINI_CHANNEL1_API_KEY",
                "GEMINI_CHANNEL2_API_KEY", "x-goog-api-key", "Bearer", "test-token"):
            assert_true(forbidden not in text,
                        "error sidecar should redact sensitive token text: " + forbidden)


def test_review_generation_limit_caps_failed_attempts():
    tool = load_module(GEMINI_TOOL, "gemini_signal_llm_review_limit")
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        cards = [card(f"CARD-{index}") for index in range(3)]
        source.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in cards),
            encoding="utf-8")
        calls = []

        def failing_card_call(api_key, model, request_body, timeout):
            del api_key, model, request_body, timeout
            calls.append("card")
            raise RuntimeError("synthetic card failure")

        result = tool.generate_reviews(
            source,
            reviews,
            api_key="test-key",
            model="gemini-3.5-flash",
            limit=1,
            include_synthetic=True,
            call_gemini=failing_card_call,
        )
        assert_true(result["errors"] == 1
                    and len(calls) == 1
                    and len(reviews.read_text(encoding="utf-8").splitlines()) == 1,
                    "card review limit should cap failed attempts, not only successful writes")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        ledger = root / "signal_transition_ledger.jsonl"
        reviews = root / "signal_transition_llm_reviews.jsonl"
        rows = []
        for index in range(3):
            item = transition_record()
            item["transition_id"] = f"tr-limit-{index}"
            item["current_card_id"] = f"CARD-{index}"
            item["current_ts_ms"] = 1781773800000 + index
            rows.append(item)
        ledger.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
            encoding="utf-8")
        calls = []

        def failing_transition_call(api_key, model, request_body, timeout):
            del api_key, model, request_body, timeout
            calls.append("transition")
            raise RuntimeError("synthetic transition failure")

        result = tool.generate_transition_reviews(
            ledger,
            reviews,
            api_key="test-key",
            model="gemini-3.5-flash",
            limit=1,
            call_gemini=failing_transition_call,
        )
        assert_true(result["attempted_transitions"] == 1
                    and result["errors"] == 1
                    and len(calls) == 1
                    and len(reviews.read_text(encoding="utf-8").splitlines()) == 1,
                    "transition review limit should cap failed attempts, not only successful writes")


def test_frontend_renders_session_context_between_rank_and_llm_review():
    app = FRONTEND_APP.read_text(encoding="utf-8")
    html = FRONTEND_HTML.read_text(encoding="utf-8")
    rank_idx = app.find("${renderGexRank(doc)}")
    session_idx = app.find("${renderSignalSessionContext(doc)}")
    transition_idx = app.find("${renderTransitionContext(doc)}")
    llm_idx = app.find("${renderLlmReview(doc)}")
    decision_idx = app.find("${renderDecision(doc)}")
    assert_true(rank_idx != -1 and session_idx != -1 and transition_idx != -1
                and llm_idx != -1,
                "rank, session context, transition, and llm render calls should exist")
    assert_true(decision_idx == -1,
                "low-signal decision conclusion should not render in the main flow")
    assert_true(rank_idx < session_idx < transition_idx < llm_idx,
                "session context should render after rank and before transition/LLM review")
    assert_true(".llm-review-panel" in html and ".llm-review-summary" in html,
                "LLM review should have prominent panel styling")
    for text in (
        "状态",
        "谨慎等级",
        "模型服务",
        "与系统结论关系",
        "支持系统结论",
        "理论主动倾向",
        "全局 Gamma 体制分析",
        "风险叠加，不是方向",
        "混合不明",
        'gemini: "Gemini"',
        "输入包哈希",
        "信号时区置信度 / 前提耐久度",
        "低转中缓冲带",
    ):
        assert_true(text in app, "LLM review should localize " + text)
    assert_true('statusBadge("Status"' not in app and 'statusBadge("Caution"' not in app,
                "LLM review badges should not expose English labels")
    assert_true("function hasLlmGammaKeyLevel(doc, key)" in app,
                "gamma overview should dedupe repeated LLM key levels by field")
    for marker in (
        "const showFlipPoint = !hasLlmGammaKeyLevel(doc, \"flip\")",
        "const showCallWall = !hasLlmGammaKeyLevel(doc, \"call_wall\")",
        "const showPutWall = !hasLlmGammaKeyLevel(doc, \"put_wall\")",
        "const showPinStrike = !hasLlmGammaKeyLevel(doc, \"pin\")",
        "const showMagnetLevel = !hasLlmGammaKeyLevel(doc, \"pin\")",
    ):
        assert_true(marker in app,
                    "gamma overview should merge repeated key level by field: " + marker)


def test_fmz_signal_loop_does_not_call_llm_in_process():
    source = SIGNAL_FILE.read_text(encoding="utf-8")
    emitter_start = source.index("    def _emit_signal_review_card")
    emitter_end = source.index("    def _emit_push_self_test", emitter_start)
    emitter = source[emitter_start:emitter_end]
    attach_start = source.index("def attach_llm_review")
    attach_end = source.index("def _extract_llm_review_payload", attach_start)
    attach = source[attach_start:attach_end]
    assert_true("attach_llm_review(" not in emitter,
                "FMZ signal card emitter should not call LLM in-process")
    assert_true(".post_json(" not in attach and "Authorization" not in attach,
                "compatibility hook should not send HTTP requests")


if __name__ == "__main__":
    test_transition_llm_mode_uses_program_delta_only_and_writes_sidecar()
    test_transition_sort_keys_accept_mixed_timestamp_types()
    test_gemini_packet_prompt_and_sidecar_generation()
    test_call_gemini_falls_back_to_channel2_only_for_retryable_errors()
    test_materializer_merges_sidecar_without_downgrading_inline_ok()
    test_generate_reviews_redacts_sensitive_error_text()
    test_review_generation_limit_caps_failed_attempts()
    test_frontend_renders_session_context_between_rank_and_llm_review()
    test_fmz_signal_loop_does_not_call_llm_in_process()
    print("signal_llm_review_pipeline: PASS")
