import importlib.util
import json
import pathlib
import tempfile


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

    prompt = tool.build_prompt(packet)
    assert_true("BLIND_THEORETICAL_PACKET" in prompt,
                "prompt should present blind theoretical packet separately")
    assert_true("FULL_AUDIT_PACKET" in prompt,
                "prompt should present full audit packet after blind section")
    assert_true("正 Gamma" in prompt and "负 Gamma" in prompt,
                "prompt should include Gamma regime lens theory")
    assert_true("不是胜率" in prompt, "prompt should reject confidence-as-win-rate")
    assert_true("不得重算模型" in prompt, "prompt should reject recomputation")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        source.write_text(json.dumps(sample, ensure_ascii=False) + "\n",
                          encoding="utf-8")

        def fake_call(api_key, model, request_body, timeout):
            assert_true(api_key == "test-key", "api key should only be passed to call")
            generation = request_body["generationConfig"]
            assert_true(generation["responseMimeType"] == "application/json",
                        "Gemini request should ask for JSON")
            schema = generation["responseSchema"]
            assert_true("summary_cn" in schema["required"], "schema required summary")
            return {"candidates": [{"content": {"parts": [
                {"text": json.dumps(model_payload(), ensure_ascii=False)}
            ]}}]}

        result = tool.generate_reviews(source, reviews, api_key="test-key",
                                       model="gemini-3.5-flash",
                                       call_gemini=fake_call,
                                       reviewed_at="2026-06-19T00:00:00+00:00")
        assert_true(result["written_reviews"] == 1, "one review written")
        saved = json.loads(reviews.read_text(encoding="utf-8"))
        review = saved["llm_review"]
        assert_true(review["provider"] == "gemini", "provider should be gemini")
        active_view = review.get("theoretical_active_view")
        assert_true(isinstance(active_view, dict), "review should include theoretical active view")
        assert_true(active_view["derived_blind"] is False,
                    "single-call theoretical view must not pretend true blind derivation")
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
            raise RuntimeError(
                "request failed with AIza" + "A" * 28
                + " GEMINI_API_KEY x-goog-api-key Bearer test-token")

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
        for forbidden in ("AIza", "GEMINI_API_KEY", "x-goog-api-key", "Bearer", "test-token"):
            assert_true(forbidden not in text,
                        "error sidecar should redact sensitive token text: " + forbidden)


def test_frontend_renders_llm_review_directly_below_rank():
    app = FRONTEND_APP.read_text(encoding="utf-8")
    html = FRONTEND_HTML.read_text(encoding="utf-8")
    rank_idx = app.find("${renderGexRank(doc)}")
    llm_idx = app.find("${renderLlmReview(doc)}")
    decision_idx = app.find("${renderDecision(doc)}")
    assert_true(rank_idx != -1 and llm_idx != -1 and decision_idx != -1,
                "rank, llm, decision render calls should exist")
    assert_true(rank_idx < llm_idx < decision_idx,
                "LLM review should render after rank and before decision")
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
    test_gemini_packet_prompt_and_sidecar_generation()
    test_materializer_merges_sidecar_without_downgrading_inline_ok()
    test_generate_reviews_redacts_sensitive_error_text()
    test_frontend_renders_llm_review_directly_below_rank()
    test_fmz_signal_loop_does_not_call_llm_in_process()
    print("signal_llm_review_pipeline: PASS")
