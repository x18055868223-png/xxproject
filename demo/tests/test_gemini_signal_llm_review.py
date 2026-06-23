import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_FILE = ROOT / "tools" / "gemini_signal_llm_review.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("gemini_signal_llm_review", TOOL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def sample_card(card_id="20260618T160357+0800-BTC-SAMPLE-3272"):
    return {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_at": "2026-06-18T16:03:57+08:00",
            "is_synthetic": False,
        },
        "market_context": {
            "price": 63339.96,
            "quote_currency": "USDT",
            "price_source": "binance_spot_volume_bar",
        },
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "evidence_strength": 44,
            "confidence": 38,
            "confidence_semantics": "EVIDENCE_QUALITY_NOT_WIN_RATE",
            "trade_allowed": False,
        },
        "reasoning": {
            "evidence": [
                {"key": "TMV", "lean": "BULLISH", "weighted_contribution": 0.16},
                {"key": "SRD", "lean": "BEARISH", "weighted_contribution": -0.03},
            ],
            "summary_cn": "sample evidence ledger",
        },
        "conflict": {
            "ratio": 0.38,
            "level": "MATERIAL",
            "aligned_keys": ["TMV"],
            "dissent_keys": ["SRD"],
        },
        "blocking": {
            "has_block": True,
            "block_kind": "SOFT_GATE",
        },
        "quality": {"overall": "OK", "missing_fields": []},
        "factor_cross_section": {
            "gex_info": {
                "market_state": "POSITIVE_GAMMA",
                "net_gamma_notional_usd": 12400000,
                "flip_point": 62800,
                "call_wall": 65000,
                "put_wall": 60000,
                "magnet_level": 64000,
                "rank": {
                    "window": {"sample_count": 42, "window_days": 14.5},
                    "metrics": {
                        "gex_board.total_net_gex": {
                            "rank_pct": 61,
                            "abs_rank_pct": 86,
                            "quality": "warming_up",
                        },
                        "volatility.iv_rv_ratio": {"rank_pct": 38},
                        "volatility.pcr": {"rank_pct": 70},
                    },
                },
            },
            "tmvf": {"direction": "BULLISH", "tmv_blend": 0.42},
            "macro_pressure": {"regime": "MILD_HEADWIND", "score": 0.23},
        },
        "delivery": {"local_jsonl": r"C:\secret\signal_review.jsonl"},
        "integrity": {"config_snapshot_hash": "sha256:should_not_leave"},
        "api_token": "sk-should-not-leave",
    }


def main():
    tool = load_tool()
    card = sample_card()
    packet = tool.build_review_packet(card)
    packet_text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert_true("api_token" not in packet_text, "packet should drop token-like keys")
    assert_true("delivery" not in packet_text, "packet should drop delivery paths")
    assert_true("integrity" not in packet_text, "packet should drop integrity hashes")
    assert_true("signal_review.jsonl" not in packet_text, "packet should not leak local paths")
    assert_true(packet["decision"]["confidence_semantics"] == "EVIDENCE_QUALITY_NOT_WIN_RATE",
                "confidence semantics should be explicit")
    blind = tool.build_blind_theoretical_packet(packet)
    blind_text = json.dumps(blind, ensure_ascii=False, sort_keys=True)
    for forbidden in ("decision", "reasoning", "conflict", "blocking", "trade_allowed"):
        assert_true(forbidden not in blind_text,
                    "blind theoretical packet should omit " + forbidden)
    assert_true("gamma_regime" in blind_text and "gex_info" in blind_text,
                "blind theoretical packet should retain gamma context")
    assert_true(blind["schema"]["derived_blind"] is True,
                "blind packet should use true two-call blind mode")

    blind_prompt = tool.build_blind_prompt(packet)
    assert_true("BLIND_THEORETICAL_PACKET" in blind_prompt,
                "blind prompt should include a blind theoretical packet")
    assert_true("FULL_AUDIT_PACKET" not in blind_prompt,
                "blind prompt should not include full audit packet")
    prompt = tool.build_prompt(packet, {
        "theoretical_active_view": {
            "bias": "BULLISH_LEAN",
            "conviction": "LOW",
            "basis_cn": "盲读理论截面偏多但不确定性高。",
            "key_drivers": ["TMV 正贡献仍在"],
            "counter_evidence": ["宏观压力和 SRD 反向"],
            "boundary_cn": "这是审计参考视角，不改变系统结论、置信度、门控或交易许可。",
        },
        "gamma_regime_lens": {
            "regime": "LONG_GAMMA_STABILIZING",
            "regime_extremity": "MEDIUM",
            "dynamics_cn": "正 Gamma 钉住倾向压制波动并吸附到 pin 附近。",
            "dominant_tail_risk_cn": "主要风险是把钉住误读为趋势突破。",
            "conviction_effect_on_directional_view": "LOWER",
            "key_levels": {"flip": 62800, "call_wall": 65000, "put_wall": 60000, "pin": 64000},
            "positioning_assumption_cn": "假设 netGEX 符号可代理做市商 Gamma 暴露。",
            "data_quality_cn": "rank 仍处于 warming_up。",
            "lens_is_risk_overlay_not_direction": True,
        },
    })
    assert_true("BLIND_REVIEW_RESULT" in prompt,
                "full prompt should include first-call blind result")
    assert_true("FULL_AUDIT_PACKET" in prompt,
                "prompt should include full audit packet for reconciliation")
    assert_true("Gamma 体制" in blind_prompt and "反身" in blind_prompt,
                "blind prompt should force Gamma regime lens reasoning")
    assert_true("不是胜率" in prompt, "prompt should prevent confidence-as-win-rate")
    assert_true("不得重算模型" in prompt, "prompt should forbid model recomputation")
    assert_true("只输出 JSON" in prompt, "prompt should require JSON-only output")

    request = tool.build_gemini_request(prompt, model="gemini-3.5-flash")
    generation = request["generationConfig"]
    assert_true(generation["responseMimeType"] == "application/json",
                "Gemini request should ask for JSON")
    required = set(generation["responseSchema"]["required"])
    for key in ("summary_cn", "agreement_with_system", "caution_level",
                "theoretical_active_view", "gamma_regime_lens",
                "main_supporting_factors", "main_risks_or_conflicts",
                "operator_focus", "invalid_if", "not_trading_advice"):
        assert_true(key in required, "schema missing " + key)
    view_schema = generation["responseSchema"]["properties"]["theoretical_active_view"]
    view_required = set(view_schema["required"])
    for key in ("bias", "conviction", "basis_cn", "key_drivers",
                "counter_evidence", "boundary_cn"):
        assert_true(key in view_required, "theoretical active view missing " + key)
    lens_schema = generation["responseSchema"]["properties"]["gamma_regime_lens"]
    lens_required = set(lens_schema["required"])
    for key in ("regime", "regime_extremity", "dynamics_cn",
                "dominant_tail_risk_cn", "conviction_effect_on_directional_view",
                "key_levels", "positioning_assumption_cn", "data_quality_cn",
                "lens_is_risk_overlay_not_direction"):
        assert_true(key in lens_required, "gamma regime lens missing " + key)

    model_payload = {
        "summary_cn": "系统给出中性等待是合理的，但冲突比例偏高，复核意见应保持谨慎。",
        "agreement_with_system": "PARTIAL_SUPPORT",
        "caution_level": "HIGH",
        "theoretical_active_view": {
            "bias": "BULLISH_LEAN",
            "conviction": "LOW",
            "basis_cn": "若只看理论截面，TMV 与正 Gamma 钉住偏向缓慢上修，但宏观逆风和 SRD 反向使把握度很低。",
            "key_drivers": ["TMV 正贡献仍在", "正 Gamma 钉住降低单边突破质量"],
            "counter_evidence": ["SRD 与宏观压力反向", "rank 仍处于 warming_up"],
            "boundary_cn": "这是审计参考视角，不改变系统结论、置信度、门控或交易许可。",
        },
        "gamma_regime_lens": {
            "regime": "LONG_GAMMA_STABILIZING",
            "regime_extremity": "MEDIUM",
            "dynamics_cn": "正 Gamma 钉住倾向压制波动并吸附到 pin 附近。",
            "dominant_tail_risk_cn": "主要风险是把钉住误读为趋势突破，或忽略 flip 附近切换。",
            "conviction_effect_on_directional_view": "LOWER",
            "key_levels": {
                "flip": 62800,
                "call_wall": 65000,
                "put_wall": 60000,
                "pin": 64000,
            },
            "positioning_assumption_cn": "假设 netGEX 符号可代理做市商 Gamma 暴露；加密市场该假设需要谨慎。",
            "data_quality_cn": "rank 仍处于 warming_up，极端程度判断只作低把握参考。",
            "lens_is_risk_overlay_not_direction": True,
        },
        "main_supporting_factors": ["TMV 仍提供同向支撑"],
        "main_risks_or_conflicts": ["SRD 与宏观轻度逆风形成反向证据"],
        "operator_focus": ["观察价格是否继续贴近 pin strike"],
        "invalid_if": ["主动流从同向转为反向"],
        "data_quality_note": "rank 样本仍在 warming up",
        "not_trading_advice": True,
    }
    parsed = tool.parse_gemini_response({
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps(model_payload, ensure_ascii=False)}]
            }
        }]
    })
    review = tool.build_llm_review(card, parsed, model="gemini-3.5-flash",
                                   reviewed_at="2026-06-19T00:00:00+00:00")
    assert_true(review["status"] == "OK", "valid model output should become OK review")
    assert_true(review["provider"] == "gemini", "provider should be gemini")
    assert_true(review["not_trading_advice"] is True, "disclaimer flag should be forced true")
    assert_true(review["theoretical_active_view"]["bias"] == "BULLISH_LEAN",
                "theoretical active view should be persisted")
    assert_true(review["theoretical_active_view"]["derived_blind"] is True,
                "two-call theoretical view should mark derived_blind true")
    assert_true(review["blind_review_mode"] == "two_call_strict",
                "review should record strict two-call blind mode")
    assert_true(review["gamma_regime_lens"]["regime"] == "LONG_GAMMA_STABILIZING",
                "gamma regime lens should be persisted")
    assert_true(review["gamma_regime_lens"]["lens_is_risk_overlay_not_direction"] is True,
                "gamma lens should remain risk overlay")
    assert_true("不改变系统结论" in review["theoretical_active_view"]["boundary_cn"],
                "theoretical active view should keep audit-only boundary")
    assert_true("decision" not in review, "review must not include nested decision override")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "llm_reviews.jsonl"
        source.write_text(json.dumps(card, ensure_ascii=False) + "\n", encoding="utf-8")

        calls = []

        def fake_call(api_key, model, request_body, timeout):
            assert_true(api_key == "test-key", "fake call receives api key")
            calls.append(request_body)
            if len(calls) == 1:
                return {
                    "candidates": [{
                        "content": {
                            "parts": [{"text": json.dumps({
                                "theoretical_active_view": model_payload["theoretical_active_view"],
                                "gamma_regime_lens": model_payload["gamma_regime_lens"],
                            }, ensure_ascii=False)}]
                        }
                    }]
                }
            return {
                "candidates": [{
                    "content": {
                        "parts": [{"text": json.dumps(model_payload, ensure_ascii=False)}]
                    }
                }]
            }

        result = tool.generate_reviews(source, reviews, api_key="test-key",
                                       model="gemini-3.5-flash", limit=5,
                                       call_gemini=fake_call,
                                       reviewed_at="2026-06-19T00:00:00+00:00")
        assert_true(result["written_reviews"] == 1, "one review should be written")
        assert_true(len(calls) == 2, "one sidecar review should use two Gemini calls")
        saved = json.loads(reviews.read_text(encoding="utf-8").strip())
        assert_true(saved["card_id"] == card["identity"]["card_id"], "sidecar card id")
        assert_true(saved["llm_review"]["summary_cn"] == model_payload["summary_cn"],
                    "sidecar should persist summary")

    print("gemini_signal_llm_review: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("gemini_signal_llm_review: FAIL - " + str(exc))
        sys.exit(1)
