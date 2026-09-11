"""Reader integration checks for advice, source boundaries and old projections."""
import copy
from test_signal_evidence_v21_frontend import v21_card, projection_hash
from test_signal_evidence_frontend import AS_OF_MS, assessment_hash, html_section
from test_signal_comfort_frontend import render_cards, assert_no_machine_leak


def card_v22():
    card = v21_card("V22-READER-SYNTHETIC")
    review = card["llm_review"]
    review["schema_version"] = "signal_llm_review@2.2.0"
    review["prompt_version"] = "signal_llm_review_prompt@2.2.0"
    adv = review["content"]["integrated_trade_advisory"]
    guidance = {
        "status": "ASSESSED", "summary_cn": "值得优先研究 Call 侧；增加空间时也要核对牺牲的净补偿。",
        "tradeoffs_cn": ["更远的卖出位置增加距离，但权利金可能不足；不能只按距离选择。"],
        "evidence_refs": ["pressure.tmv.direction"], "validation_reasons_cn": [],
        "outlooks": [
            {"horizon_hours": 4, "scenario_cn": "近端反弹与较长窗口下行可以并存。", "watch_cn": "观察主动买入是否持续传导。", "evidence_refs": ["pressure.tmv.direction"]},
            {"horizon_hours": 24, "scenario_cn": "更长窗口关注结构迁移及竞争机制。", "watch_cn": "边界移动后重新解释空间。", "evidence_refs": ["structure.distance.call_wall_pct"]},
        ],
    }
    boundary = {"label_cn": "原信号窗口与风险", "summary_cn": "原信号窗口尚未开放；不改变机器权限。人工研究建议保持独立。", "sides": {
        key: {"state": "WAIT", "label_cn": "窗口未开", "reasons_cn": ["原信号窗口尚未开放；不改变机器权限。"]}
        for key in ("put_credit", "call_credit")}}
    adv.update(advisory_guidance=guidance, source_boundary=boundary,
               action_summary_cn=guidance["summary_cn"], display_action_summary_cn=guidance["summary_cn"])
    for key, side in adv["side_evidence_ratings"].items():
        side["fit_thesis"] = "put_downside_containment" if key == "put_credit" else "call_upside_containment"
        side["mechanism"] = {"summary_cn": side["mechanism_cn"], "refs": side.get("evidence_refs", [])}
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    review["content"]["integrated_trade_advisory"] = copy.deepcopy(adv)
    summary = card["signal_evidence_summary"]
    summary.update(schema="signal_evidence_summary@2.2.0", display_projection_version="2.2.0",
                   advisory_guidance=copy.deepcopy(guidance), source_boundary=copy.deepcopy(boundary),
                   action_summary_cn=guidance["summary_cn"], display_action_summary_cn=guidance["summary_cn"],
                   assessment_hash=adv["validation"]["assessment_hash"])
    summary["display_projection_hash"] = projection_hash(summary)
    return card


def test_v22_advice_and_risk_share_result_without_old_gate_headline():
    rendered = render_cards([card_v22()])
    top = html_section(rendered["documentHtml"], "signal-comfort")
    assert "本卡建议" in top and "值得优先研究 Call 侧" in top
    assert "关键取舍" in top and "未来四小时" in top and "未来二十四小时" in top
    assert "原信号窗口与风险" in top and "窗口未开" in top
    assert "当前没有一侧进入人工准备" not in top
    assert top.count("原信号窗口尚未开放；不改变机器权限。") == 1
    assert "值得优先研究 Call 侧" in rendered["indexText"]
    assert "当前事实是否支持下行侵入风险受到可解释约束" in rendered["documentText"]
    assert "当前事实是否支持上行侵入风险受到可解释约束" in rendered["documentText"]
    assert_no_machine_leak(rendered, context="v2.2 guidance and source boundary")


def test_v22_summary_mismatch_is_not_silently_used():
    card = card_v22()
    card["signal_evidence_summary"]["advisory_guidance"]["summary_cn"] = "不一致的建议"
    card["signal_evidence_summary"]["display_projection_hash"] = projection_hash(card["signal_evidence_summary"])
    rendered = render_cards([card])
    assert "建议或原信号边界的摘要与详情不一致" in rendered["documentText"]


def test_old_v21_remains_readable():
    rendered = render_cards([v21_card()])
    assert "Put B级" in rendered["indexText"] and "Call B级" in rendered["indexText"]
    assert "发布投影版本未通过" not in rendered["documentText"]


def test_new_option_units_and_location_do_not_become_flow_or_gamma_observations():
    card = card_v22()
    adv = card["llm_review"]["content"]["integrated_trade_advisory"]
    template = copy.deepcopy(adv["market_facts"][0])
    for fact_id, label, value, unit in [
        ("structure.options.24h.hours_to_expiry", "实际剩余期限", 17.5, "hours"),
        ("pressure.options.24h.atm_iv_pct", "近期期权隐含波动率", 40.98, "%"),
        ("pressure.volatility.dvol", "DVOL 波动背景", 40.47, "DVOL"),
        ("structure.gamma.regime", "Gamma 位置分类", "POSITIVE_GAMMA", None),
    ]:
        fact = copy.deepcopy(template)
        fact.update(id=fact_id, label_cn=label, value=value, unit=unit,
                    topic="adverse_pressure", source_group="options", summary_cn="",
                    provenance={"method": "ggr_price_location_regime"})
        adv["market_facts"].append(fact)
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    card["signal_evidence_summary"]["assessment_hash"] = adv["validation"]["assessment_hash"]
    card["signal_evidence_summary"]["display_projection_hash"] = projection_hash(card["signal_evidence_summary"])
    rendered = render_cards([card])
    structure = html_section(rendered["documentHtml"], "market-options-structure")
    assert "17.5 小时" in structure and "近期期权隐含波动率" in structure and "DVOL 波动背景" in structure
    assert "位于翻转位上方" in structure
    for value, expected in [("正 Gamma", "位于翻转位上方"), ("负 Gamma", "位于翻转位下方"), ("Gamma 过渡区", "位于翻转过渡区")]:
        adv["market_facts"][-1]["value"] = value
        refresh_reader_hashes(card)
        structure = html_section(render_cards([card])["documentHtml"], "market-options-structure")
        assert expected in structure, (value, expected)


def refresh_reader_hashes(card):
    adv = card["llm_review"]["content"]["integrated_trade_advisory"]
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    summary = card["signal_evidence_summary"]
    summary["assessment_hash"] = adv["validation"]["assessment_hash"]
    summary["advisory_guidance"] = copy.deepcopy(adv["advisory_guidance"])
    summary["display_projection_hash"] = projection_hash(summary)


def test_optional_outlooks_have_no_placeholder_or_empty_column():
    for count in (0, 1, 2):
        card = card_v22()
        guidance = card["llm_review"]["content"]["integrated_trade_advisory"]["advisory_guidance"]
        guidance["outlooks"] = guidance["outlooks"][:count]
        refresh_reader_hashes(card)
        html = html_section(render_cards([card])["documentHtml"], "signal-comfort")
        assert html.count('class="evidence-next-side"') == count
        assert ('evidence-guidance-outlooks is-single' in html) == (count == 1)
        assert ('evidence-guidance-outlooks' in html) == bool(count)
        assert "情景依据不足" not in html and "等待有效观察" not in html


def test_near_reading_uses_bound_raw_values_without_mutating_facts():
    card = card_v22()
    card["identity"]["confirmed_time_ms"] = AS_OF_MS
    window = {"observed_start_ms": AS_OF_MS-900000, "observed_end_ms": AS_OF_MS-1,
              "requested_start_ms": AS_OF_MS-900000, "requested_end_ms": AS_OF_MS-1,
              "bar_count": 15, "expected_bar_count": 15, "missing_minutes": 0,
              "range_pct": 0.1545, "high_excursion_pct": 0.1488, "low_excursion_pct": -0.0057,
              "distance_from_high_pct": 0.008, "distance_from_low_pct": 0.1465}
    card["near_term_market_context"] = {"schema_version": "1.0.0", "as_of_ms": AS_OF_MS, "windows": {"15m": window}}
    adv = card["llm_review"]["content"]["integrated_trade_advisory"]
    for suffix, label, value in [("coverage", "近端15分钟行情覆盖", "可用"), ("range_profile", "近端15分钟区间位置", "区间振幅 0.1545%；上探 +0.1488%；下探 -0.0057%；距高点 0.008%；距低点 0.1465%")]:
        fact = copy.deepcopy(adv["market_facts"][0])
        fact.update(id="response.near_term.15m."+suffix, label_cn=label, value=value, unit=None,
                    topic="price_response", summary_cn="原有长摘要保存在详情。", usable=True,
                    observed_at_ms=AS_OF_MS-1, provenance={"selected_source": "near_term_market_context", "time_basis": "selected_observation_time"})
        adv["market_facts"].append(fact)
    refresh_reader_hashes(card)
    before = copy.deepcopy(card)
    html = html_section(render_cards([card])["documentHtml"], "market-price-path")
    assert 'is-text-value' in html and 'market-fact-metrics' in html
    assert "已闭合 15 / 15 根，缺口 0 分钟" in html and "北京时间" in html
    assert "振幅 0.1545%" in html and "距低 0.1465%" in html
    assert card == before
    card["near_term_market_context"]["as_of_ms"] += 60000
    html = html_section(render_cards([card])["documentHtml"], "market-price-path")
    assert "已闭合 15 / 15 根" not in html, "Do not borrow a different card's raw window"


if __name__ == "__main__":
    test_v22_advice_and_risk_share_result_without_old_gate_headline()
    test_v22_summary_mismatch_is_not_silently_used()
    test_old_v21_remains_readable()
    test_new_option_units_and_location_do_not_become_flow_or_gamma_observations()
    test_optional_outlooks_have_no_placeholder_or_empty_column()
    test_near_reading_uses_bound_raw_values_without_mutating_facts()
    print("signal_evidence_v22_frontend: PASS")
