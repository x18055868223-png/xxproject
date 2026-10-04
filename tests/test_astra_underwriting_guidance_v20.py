import copy
import json

from tools import astra_underwriting_guidance_v20 as guidance
from tools import astra_underwriting_fact_context_v20 as fact_context
from tools import astra_underwriting_v20 as underwriting
from tools import astra_workbench_preview_v20 as preview

from test_astra_underwriting_v20 import sample
from test_astra_workbench_v20 import T, make_workbench, native_gate, positive_risk, write_fmz


def _texts(result):
    return "\n".join(note["text_cn"] for note in result["notes"])


def test_responsibility_direction_is_side_specific():
    put = underwriting.evaluate(sample("put"))
    call = underwriting.evaluate(sample("call"))

    put_guidance = guidance.build_reading_guidance(put, put["fact_context"])
    call_guidance = guidance.build_reading_guidance(call, call["fact_context"])

    assert "Put 信用价差" in _texts(put_guidance)
    assert "下跌" in _texts(put_guidance)
    assert "Call 信用价差" in _texts(call_guidance)
    assert "上涨" in _texts(call_guidance)
    assert "卖出腿至保护腿之间" in _texts(call_guidance)
    assert "不声称赔付持续单调增加" in _texts(call_guidance)


def test_non_positive_credit_and_missing_risk_block_ev_sorting():
    snapshot = underwriting.evaluate(sample("put", risk=False))
    snapshot["economics"]["net_credit_after_entry_fee_btc"] = 0

    result = guidance.build_reading_guidance(snapshot, snapshot["fact_context"])
    text = _texts(result)

    assert result["status"] == "synthetic"
    assert "净信用非正" in text
    assert "补偿不足" in text
    assert "冻结风险均值缺失" in text
    assert "不能按 EV" in text


def test_edb_neutral_conflict_is_same_source_not_confirmation():
    snapshot = underwriting.evaluate(sample("put"))
    fact = copy.deepcopy(snapshot["fact_context"])
    fact["status"] = "current"
    fact["market_facts"] = {
        "flow": {"direction": "NEUTRAL"},
        "edb_reasoning": {
            "lean": "NEUTRAL",
            "side_hint": "CALL_CREDIT",
            "conflict_level": "MATERIAL",
            "summary_cn": "测试冲突态",
        },
    }

    result = guidance.build_reading_guidance(snapshot, fact)
    text = _texts(result)

    assert "中性或存在冲突" in text
    assert "价格倾向：中性" in text
    assert "源侧别：Call 侧" in text
    assert "冲突强度：明显" in text
    assert "未识别" not in text
    assert "同源市场意见" in text
    assert "不构成独立确认" in text
    assert "不默认跟随" in text
    assert "lean=" not in text
    assert "side_hint=" not in text
    assert "flow=" not in text
    assert "conflict=" not in text
    assert "CALL_CREDIT" not in text
    assert "MATERIAL" not in text
    assert "NEUTRAL" not in text


def test_flow_only_does_not_create_edb_confirmation():
    snapshot = underwriting.evaluate(sample("put"))
    fact = copy.deepcopy(snapshot["fact_context"])
    fact["status"] = "current"
    fact["market_facts"] = {"flow": {"direction": "NEUTRAL"}}

    result = guidance.build_reading_guidance(snapshot, fact)
    edb_notes = [note for note in result["notes"] if note["kind"].startswith("edb")]

    assert edb_notes and edb_notes[-1]["kind"] == "edb_gap"
    assert "EDB 原方向缺失" in edb_notes[-1]["text_cn"]
    assert "NEUTRAL" not in _texts(result)


def test_flow_only_real_fact_projection_is_edb_gap():
    row = {
        "schema_name": "EvaluationSnapshot",
        "schema_version": "1.6.2",
        "decision": {
            "ts_ms": T - 1000,
            "demo_version": "2.0.0",
            "symbol": "BTC",
            "runtime_facts": {
                "runtime_mode": "live_public_read_only",
                "current_price": 100000,
                "current_price_source": "binance_spot",
                "current_price_observed_ms": T - 1500,
            },
        },
        "factor_snapshot": {
            "flow": {"direction": "NEUTRAL", "tmv_blend": 0},
            "neutral_repair_signal": {"state": "IDLE", "is_active": False},
        },
    }
    projected = fact_context.from_evaluation_snapshot(row, T, source_hash="c" * 64)
    snapshot = underwriting.evaluate(sample("put"))

    result = guidance.build_reading_guidance(snapshot, projected)
    text = _texts(result)

    assert projected["market_facts"]["edb_reasoning"]["interpretation"] == "evidence_quality_not_win_rate"
    assert result["notes"][-1]["kind"] == "edb_gap"
    assert "EDB 原方向缺失" in text
    assert "流向" not in text
    assert "NEUTRAL" not in text


def test_edb_direction_fallback_and_score_without_direction_boundaries():
    put = underwriting.evaluate(sample("put"))
    direction_fact = {
        "status": "current",
        "market_facts": {"edb": {"direction": "DOWN", "edb_score": -0.4}},
    }
    score_only_fact = {
        "status": "current",
        "market_facts": {
            "flow": {"direction": "DOWN"},
            "edb": {"edb_score": -0.4},
            "edb_reasoning": {"interpretation": "evidence_quality_not_win_rate", "reason_codes": []},
        },
    }

    direction_text = _texts(guidance.build_reading_guidance(put, direction_fact))
    score_only_text = _texts(guidance.build_reading_guidance(put, score_only_fact))

    assert "价格倾向：偏空" in direction_text
    assert "对本 Put 信用价差不利" in direction_text
    assert "DOWN" not in direction_text
    assert "EDB 字段存在但原方向缺失" in score_only_text
    assert "不能用资金/价格流向补成 EDB 确认" in score_only_text
    assert "flow" not in score_only_text
    assert "DOWN" not in score_only_text


def test_default_fact_context_reads_snapshot_fmz_fact_context():
    snapshot = underwriting.evaluate(sample("call"))
    snapshot.pop("fact_context", None)
    snapshot["fmz_fact_context"] = {
        "status": "current",
        "market_facts": {"edb_reasoning": {"lean": "BULLISH", "side_hint": "CALL_CREDIT"}},
    }

    text = _texts(guidance.build_reading_guidance(snapshot))

    assert "价格倾向：偏多" in text
    assert "源侧别：Call 侧" in text
    assert "对本 Call 信用价差不利" in text


def test_edb_price_lean_flags_adverse_side_without_selecting_direction():
    call = underwriting.evaluate(sample("call"))
    fact = copy.deepcopy(call["fact_context"])
    fact["status"] = "current"
    fact["market_facts"] = {
        "edb_reasoning": {
            "lean": "BULLISH",
            "side_hint": "CALL_CREDIT",
            "conflict_level": "LOW",
        },
    }

    text = _texts(guidance.build_reading_guidance(call, fact))

    assert "价格倾向：偏多" in text
    assert "源侧别：Call 侧" in text
    assert "冲突强度：低" in text
    assert "对本 Call 信用价差不利" in text
    assert "不自动选侧" in text
    assert "BULLISH" not in text
    assert "CALL_CREDIT" not in text


def test_producer_conflict_levels_keep_chinese_semantics():
    snapshot = underwriting.evaluate(sample("call"))

    def text_for(level):
        fact = {
            "status": "current",
            "market_facts": {
                "edb_reasoning": {
                    "lean": "BULLISH",
                    "side_hint": "CALL_CREDIT",
                    "conflict_level": level,
                },
            },
        }
        return _texts(guidance.build_reading_guidance(snapshot, fact))

    none_text = text_for("NONE")
    mild_text = text_for("MILD")
    material_text = text_for("MATERIAL")
    severe_text = text_for("SEVERE")

    assert "冲突强度：无" in none_text
    assert "中性或存在冲突" not in none_text
    assert "冲突强度：轻微" in mild_text
    assert "中性或存在冲突" in mild_text
    assert "冲突强度：明显" in material_text
    assert "冲突强度：严重" in severe_text
    assert "未识别" not in "\n".join([none_text, mild_text, material_text, severe_text])


def test_identity_status_separates_synthetic_and_historical_background():
    synthetic = underwriting.evaluate(sample("put"))
    synthetic_text = _texts(guidance.build_reading_guidance(synthetic, synthetic["fact_context"]))

    historical = copy.deepcopy(synthetic)
    historical["data_identity"] = {"kind": "historical_replay", "source_sha256": "a" * 64}
    historical_result = guidance.build_reading_guidance(historical, {"status": "current", "market_facts": {}})

    assert "模拟/预览身份" in synthetic_text
    assert "旧样本" not in synthetic_text
    assert historical_result["status"] == "background"
    assert "背景或旧版事实" in _texts(historical_result)


def test_supported_margin_interpretation_uses_original_value_without_new_thresholds():
    snapshot = underwriting.evaluate(sample("put"))
    snapshot["economics"]["mu_ref_btc"] = 0.001

    positive = copy.deepcopy(snapshot)
    positive["economics"]["reference_margin_btc"] = 0.001
    zero = copy.deepcopy(snapshot)
    zero["economics"]["reference_margin_btc"] = 0
    negative = copy.deepcopy(snapshot)
    negative["economics"]["reference_margin_btc"] = -0.001
    nan_margin = copy.deepcopy(snapshot)
    nan_margin["economics"]["reference_margin_btc"] = float("nan")

    assert "M_ref 为正" in _texts(guidance.build_reading_guidance(positive, positive["fact_context"]))
    assert "不证明 EV" in _texts(guidance.build_reading_guidance(positive, positive["fact_context"]))
    assert "M_ref 为零" in _texts(guidance.build_reading_guidance(zero, zero["fact_context"]))
    assert "M_ref 为负" in _texts(guidance.build_reading_guidance(negative, negative["fact_context"]))
    assert "M_ref 缺失" in _texts(guidance.build_reading_guidance(nan_margin, nan_margin["fact_context"]))


def test_preview_synthetic_mu_is_readable_but_not_real_model_qualification():
    fixture = preview.build_preview(now=T, scenario_id="W2")
    text = _texts(fixture["candidates"][0]["reading_guidance"])

    assert "模拟 μ_ref/M_ref 已提供，非真实模型资格" in text
    assert "M_ref 为正" in text
    assert "冻结风险均值缺失" not in text

    missing = preview.build_preview(now=T, scenario_id="MISSING_MU")
    missing_text = _texts(missing["candidates"][0]["reading_guidance"])

    assert "冻结风险均值缺失" in missing_text
    assert "模拟 μ_ref/M_ref 已提供" not in missing_text


def test_build_reading_guidance_does_not_mutate_inputs():
    snapshot = underwriting.evaluate(sample("call"))
    fact = copy.deepcopy(snapshot["fact_context"])
    original_snapshot = copy.deepcopy(snapshot)
    original_fact = copy.deepcopy(fact)

    guidance.build_reading_guidance(snapshot, fact)

    assert snapshot == original_snapshot
    assert fact == original_fact


def test_workbench_candidates_and_preview_attach_reading_guidance(tmp_path):
    wb, snapshots = make_workbench(tmp_path)
    write_fmz(snapshots, native_gate())

    view = wb.refresh(now_ms=T, record_daily=False)
    fixture = preview.build_preview(now=T)

    for item in view["candidates"] + fixture["candidates"]:
        result = item["reading_guidance"]
        assert result["schema"] == guidance.SCHEMA
        assert result["scope"] == "read_only_candidate_interpretation"
        assert result["llm_call_count"] == 0
        assert result["execution_allowed"] is False
        assert isinstance(result["notes"], list)

    assert json.dumps(view, ensure_ascii=False)


def test_guidance_does_not_change_backend_or_preview_gates(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    write_fmz(snapshots, native_gate())

    view = wb.refresh(now_ms=T, record_daily=False)
    assert view["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    assert view["space_gate"]["eligible"] is True
    for item in view["candidates"]:
        assert item["review_gate"]["enabled"] is True
        assert item["review_gate"]["reasons"] == []
        assert item["reading_guidance"]["status"] == "current"

    fixture = preview.build_preview(now=T)
    assert fixture["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    assert fixture["space_gate"]["eligible"] is True
    for item in fixture["candidates"]:
        assert item["review_gate"] == {
            "enabled": True,
            "reasons": [],
            "kind": "synthetic_research_only",
            "production_attach_allowed": False,
        }
        assert item["reading_guidance"]["status"] == "synthetic"
