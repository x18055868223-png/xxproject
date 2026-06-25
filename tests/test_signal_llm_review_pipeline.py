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
                "fact_cn": "macro_score 上升 0.4279，US10Y 压力由负转正。",
                "materiality": "CRITICAL",
            }
        ],
        "cross_factor_interactions": [
            "宏观压力与 Funding 拥挤同步变差，构成多域风险趋同。"
        ],
        "candidate_causal_hypotheses": [
            {
                "hypothesis_cn": "外生风险压力可能降低原偏多前提耐久性。",
                "supporting_fact_ids": ["MACRO_SHOCK", "US10Y_SIGN_FLIP"],
                "alternative_explanations_cn": ["两张卡可能共同响应同一上游冲击。"],
                "confidence": "MEDIUM",
            }
        ],
        "anomaly_assessment": {
            "state": "REGIME_SHIFT",
            "basis_cn": "宏观分数变化超过材料阈值。"
        },
        "operator_focus": ["观察 GGR 是否继续转向放大。"],
        "invalid_if": ["两张卡不属于可比较市场阶段。"],
        "language_guard": {
            "distinguishes_observation_from_causality": True,
            "no_external_data": True,
            "no_trading_instruction": True,
        },
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
    assert_true("只解释程序已经计算出的 delta" in prompt,
                "transition prompt should forbid LLM recalculation")
    assert_true("相关性等于因果" in prompt,
                "transition prompt should guard causal certainty")
    assert_true("不得输出交易建议" in prompt,
                "transition prompt should forbid trading advice")
    assert_true("SignalTransitionReviewPacket" in prompt,
                "transition prompt should name the packet boundary")
    assert_true("core_skeleton" in prompt and "domain_change_summaries" in prompt,
                "transition prompt should anchor the LLM to skeleton and grouped summaries")

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
        assert_true(review["schema_version"] == "signal_transition_llm_review@1.0.0",
                    "transition LLM schema version")
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

        bad_payload = transition_model_payload()
        bad_payload["language_guard"]["no_external_data"] = False
        try:
            tool.build_transition_llm_review(
                transition,
                bad_payload,
                model="gemini-3.5-flash",
                reviewed_at="2026-06-19T00:00:00+00:00",
            )
            raise AssertionError("false no_external_data guard should be rejected")
        except ValueError:
            pass

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
    test_gemini_packet_prompt_and_sidecar_generation()
    test_call_gemini_falls_back_to_channel2_only_for_retryable_errors()
    test_materializer_merges_sidecar_without_downgrading_inline_ok()
    test_generate_reviews_redacts_sensitive_error_text()
    test_frontend_renders_session_context_between_rank_and_llm_review()
    test_fmz_signal_loop_does_not_call_llm_in_process()
    print("signal_llm_review_pipeline: PASS")
