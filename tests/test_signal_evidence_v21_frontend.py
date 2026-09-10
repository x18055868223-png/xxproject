import copy
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_signal_comfort_frontend import assert_no_machine_leak, assert_true, fixture_valid_as, render_cards
from test_signal_evidence_frontend import (
    AS_OF_MS,
    advisory,
    assessment_hash,
    evidence_card,
    evidence_summary,
    html_section,
)


def projection_hash(summary_payload):
    payload = copy.deepcopy(summary_payload)
    payload.pop("display_projection_hash", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def v21_price_bias(bias="BEARISH"):
    return {
        "schema": "price_bias@1.0.0",
        "status": "ASSESSED",
        "bias": bias,
        "basis_cn": "量价倾向和主动流倾向均偏下，当前独立价格结论偏空。",
        "counter_cn": "若价格重新回到上方结构并伴随主动买入增强，偏空解释需要降低权重。",
        "invalid_if_cn": "价格收回关键结构参照且主动流转向时重新判断。",
        "evidence_refs": ["pressure.tmv.direction", "pressure.cvd.combined_direction"],
        "counter_evidence_refs": ["structure.distance.call_wall_pct"],
        "validation_reasons_cn": [],
    }


def side_roles(side_key):
    if side_key == "put_credit":
        return [
            {
                "ref": "structure.distance.put_wall_pct",
                "role": "supports_fit",
                "claim_cn": "下方 Put 墙仍有距离，说明下行侵入还有空间约束背景。",
            },
            {
                "ref": "pressure.tmv.direction",
                "role": "counters_fit",
                "claim_cn": "量价主干下行是 Put 侧不利推进，不能被当作正面支持。",
            },
            {
                "ref": "structure.gex.net_gamma_notional_usd",
                "role": "context_only",
                "claim_cn": "净 Gamma 只说明波动反馈背景，不作为额外独立票。",
            },
        ]
    return [
        {
            "ref": "structure.distance.call_wall_pct",
            "role": "supports_fit",
            "claim_cn": "上方 Call 墙距离较近，可作为上行侵入受约束的空间背景。",
        },
        {
            "ref": "response.flow_price.relation",
            "role": "supports_fit",
            "claim_cn": "下行压力未形成上行推进，Call 侧不利方向暂未被触发。",
        },
        {
            "ref": "pressure.cvd.combined_direction",
            "role": "context_only",
            "claim_cn": "主动成交用于解释当前方向背景，不重复计为结构确认。",
        },
    ]


def v21_advisory(*, put_grade="B", call_grade="B", comparison_relative="call_credit", comparison_status="ASSESSED"):
    adv = advisory(put_grade=put_grade, call_grade=call_grade, action_summary="当前没有一侧进入人工准备；B 级侧启动关注。")
    ratings = adv["side_evidence_ratings"]
    ratings["put_credit"].update(
        fit_thesis="当前事实是否支持下行侵入风险受到可解释约束。",
        mechanism_cn="Put 侧需要证明下方侵入压力受空间结构或价格响应约束，而不是把下行压力本身当成利好。",
        basis_cn="下方空间仍在，但量价与主动流偏下，支持与竞争解释并存，因此只是 B 级关注。",
        market_counter_cn="量价主干和主动流均偏下，说明 Put 侧不利方向已经出现实质推进。",
        alternative_cn="也可能是下行突破前的短暂停顿，空间距离不能证明墙位承接。",
        strengthen_if_cn=["下行压力继续传导受阻，且价格未有效逼近或穿过下方 Put 墙。"],
        weaken_if_cn=["价格继续靠近或穿过 Put 墙，并伴随主动卖出维持。"],
        evidence_roles=side_roles("put_credit"),
    )
    ratings["call_credit"].update(
        fit_thesis="当前事实是否支持上行侵入风险受到可解释约束。",
        mechanism_cn="Call 侧需要证明上行侵入风险受空间结构约束；当前偏空压力对 Call 侧不利侵入并未增强。",
        basis_cn="上方墙位更近，且量价与主动流没有给出上行推进，Call 侧在同为 B 时相对更顺。",
        market_counter_cn="若宏观或主动买入突然转强，上方近墙反而会成为被快速检验的位置。",
        alternative_cn="当前也可能只是低波动整理，缺少足够证据进入 A。",
        strengthen_if_cn=["价格仍在上方墙下方整理，主动买入没有恢复。"],
        weaken_if_cn=["价格向上靠近 Call 墙且主动买入同步增强。"],
        evidence_roles=side_roles("call_credit"),
    )
    adv["price_bias"] = v21_price_bias()
    adv["side_comparison"] = {
        "status": comparison_status,
        "relative_side": comparison_relative,
        "basis_cn": "两侧同为 B，但当前量价和主动流均偏下，Call 侧面对的不利上行侵入较弱；Put 侧承受更直接的下行推进。",
        "evidence_refs": ["pressure.tmv.direction", "pressure.cvd.combined_direction", "structure.distance.call_wall_pct"],
        "flip_if_cn": ["主动买入恢复并推动价格重新逼近上方 Call 墙时，Call 侧相对优势会减弱。"],
        "validation_reasons_cn": [],
    }
    adv["display_action_summary_cn"] = "当前没有一侧进入人工准备；B 级侧启动关注。"
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return adv


def side_summary(side):
    return {
        "grade": side["grade"],
        "status": side["status"],
        "basis_cn": side["basis_cn"],
    }


def v21_summary(adv, *, market_status="AVAILABLE", price=79538.07, unit="USDT", comparison=None, hash_override=None):
    summary = {
        "schema": "signal_evidence_summary@2.1.0",
        "put_credit": side_summary(adv["side_evidence_ratings"]["put_credit"]),
        "call_credit": side_summary(adv["side_evidence_ratings"]["call_credit"]),
        "local_action_state": copy.deepcopy(adv["local_action_state"]),
        "action_summary_cn": adv["action_summary_cn"],
        "display_projection_version": "2.1.0",
        "display_action_state": copy.deepcopy(adv["local_action_state"]),
        "display_action_summary_cn": adv.get("display_action_summary_cn", adv["action_summary_cn"]),
        "source_record_hash": "sha256:source-record-v21",
        "market_snapshot": {
            "status": market_status,
            "price": price,
            "unit": unit,
            "observed_at_ms": AS_OF_MS,
            "reason_cn": "卡片时点价格由发布投影提供。" if market_status == "AVAILABLE" else "发布投影未提供卡片时点价格。",
        },
        "side_comparison": copy.deepcopy(comparison if comparison is not None else adv.get("side_comparison", {})),
        "as_of_ms": AS_OF_MS,
        "input_packet_hash": "packet-hash-hidden-in-reader",
        "assessment_hash": adv["validation"]["assessment_hash"],
        "price_bias": copy.deepcopy(adv.get("price_bias", {})),
    }
    summary["display_projection_hash"] = hash_override or projection_hash(summary)
    return summary


def v21_card(card_id="EVIDENCE-V21", **kwargs):
    adv = kwargs.pop("advisory_payload", None) or v21_advisory(**kwargs)
    summary = v21_summary(adv)
    card = evidence_card(card_id, advisory_payload=adv, summary_payload=summary)
    card["identity"]["strategy_version"] = "2.1.0"
    card["identity"]["source_record_hash"] = "sha256:source-record-v21"
    card["llm_review"]["schema_version"] = "signal_llm_review@2.1.0"
    card["llm_review"]["prompt_version"] = "signal_llm_review_prompt@2.1.0"
    card["llm_review"]["content"]["integrated_trade_advisory"] = adv
    return card


def test_v21_list_header_and_decision_use_projection_price_bias_and_comparison():
    rendered = render_cards([v21_card()])
    text = rendered["documentText"]
    index = rendered["indexText"]
    header = html_section(rendered["documentHtml"], "signal-comfort")
    assert_true("价格 79,538.07 USDT" in index, "list should show card-time projected price")
    assert_true("倾向 偏空" in index and "Put B级" in index and "Call B级" in index,
                "list should show LLM bias and both side grades")
    assert_true("Call 侧相对更有依据" in index,
                "list should use adopted server-side comparison without changing grades")
    assert_true("79,538.07 USDT · 倾向 偏空 · Call 侧相对更有依据" in text,
                "header quickline should show price, bias and comparison")
    assert_true("卡时价格" in header and "LLM 价格倾向" in header and "两侧比较" in header,
                "top decision should include compact market snapshot, bias and comparison cells")
    assert_true("偏空" in header and "Call 侧相对更有依据" in header,
                "top decision should show the model's explicit bias and adopted comparison")
    assert_true("C 级侧" not in text and "C级侧" not in text,
                "double-B summaries should not invent a C-side status")
    assert_no_machine_leak(rendered, context="v2.1 list header comparison")
    next_section = html_section(rendered["documentHtml"], "signal-next-conditions")
    assert_true("增强该侧适配" in next_section and "削弱该侧适配" in next_section,
                "v2.1 sides should keep fixed-fit strengthen/weaken wording")
    assert_true("旧版失效条件（评审对象未区分）" not in next_section,
                "v2.1 sides should not be shown as legacy ambiguous conditions")


def test_legacy_sidebar_headline_localizes_weak_direction_tokens():
    bearish = fixture_valid_as()
    bearish["identity"]["card_id"] = "LEGACY-BEARISH-WEAK"
    bearish["identity"]["short_id"] = "LBW"
    bearish["decision"]["lean"] = "BEARISH_WEAK"
    bearish["decision_matrix"]["direction"] = "BEARISH_WEAK"

    bullish = fixture_valid_as()
    bullish["identity"]["card_id"] = "LEGACY-BULLISH-WEAK"
    bullish["identity"]["short_id"] = "LUW"
    bullish["decision"]["lean"] = "BULLISH_WEAK"
    bullish["decision_matrix"]["direction"] = "BULLISH_WEAK"

    rendered = render_cards([bearish, bullish])
    combined = rendered["indexText"] + " " + rendered["indexHtml"]
    assert_true("弱偏空" in combined and "弱偏多" in combined,
                "legacy sidebar should localize weak directional headline tokens")
    assert_true("BEARISH_WEAK" not in combined and "BULLISH_WEAK" not in combined,
                "legacy sidebar should not leak weak directional enum tokens")


def test_v21_evidence_roles_drive_source_groups_without_machine_role_tokens():
    rendered = render_cards([v21_card()])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    review = html_section(html, "signal-llm-review")
    assert_true("证据角色" in text and "支持适配" in text and "反对适配" in text and "背景说明" in text,
                "reader should expose evidence roles in Chinese")
    assert_true("支持来源" in text and "反对来源" in text and "背景来源" in text,
                "role-derived refs should feed existing Chinese source groups")
    assert_true("空间结构" in text and "价格表现" in text and "主动成交" in text,
                "source chips should stay reader-level and Chinese")
    for token in ("supports_fit", "counters_fit", "context_only", "structure.distance.put_wall_pct", "pressure.tmv.direction"):
        assert_true(token not in review and token not in text and token not in html,
                    "evidence role implementation token should not leak: " + token)
    assert_no_machine_leak(rendered, context="v2.1 evidence roles")


def test_v21_invalid_side_comparison_isolated_from_side_grades():
    adv = v21_advisory(put_grade="C", call_grade="A", comparison_relative="put_credit")
    adv["action_summary_cn"] = "Call 侧证据等级 A；Put 侧普通观察。"
    adv["display_action_summary_cn"] = adv["action_summary_cn"]
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    card = evidence_card("EVIDENCE-V21-BAD-CMP", advisory_payload=adv, summary_payload=v21_summary(adv))
    card["llm_review"]["schema_version"] = "signal_llm_review@2.1.0"
    card["llm_review"]["prompt_version"] = "signal_llm_review_prompt@2.1.0"
    rendered = render_cards([card])
    text = rendered["documentText"]
    top = html_section(rendered["documentHtml"], "signal-comfort")
    assert_true("Put C级" in rendered["indexText"] and "Call A级" in rendered["indexText"],
                "invalid comparison must not erase side grades")
    assert_true("相对比较未采纳" in top and "比较侧别与两侧等级顺序矛盾" in top,
                "contradictory comparison should be isolated with a Chinese reason")
    assert_true("Put 侧相对更有依据" not in text,
                "contradictory relative side should not be displayed as adopted")
    assert_no_machine_leak(rendered, context="v2.1 invalid comparison")


def test_v21_unavailable_comparison_hides_raw_basis():
    adv = v21_advisory()
    adv["side_comparison"] = {
        "status": "UNAVAILABLE",
        "relative_side": "call_credit",
        "basis_cn": "Call 侧相对更有依据，这句未采纳结论不应显示。",
        "evidence_refs": ["pressure.tmv.direction"],
        "flip_if_cn": ["若主动买入恢复则重新比较。"],
        "validation_reasons_cn": ["比较引用暂不可核验。"],
    }
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    rendered = render_cards([v21_card("EVIDENCE-V21-CMP-UNAVAILABLE", advisory_payload=adv)])
    combined = rendered["documentText"] + " " + rendered["indexText"]
    top = html_section(rendered["documentHtml"], "signal-comfort")
    assert_true("相对比较未采纳" in top and "比较引用暂不可核验" in top,
                "unavailable comparison should show only status and validation reason")
    assert_true("这句未采纳结论不应显示" not in combined and "Call 侧相对更有依据" not in combined,
                "unavailable comparison raw basis must not leak as an adopted conclusion")
    assert_no_machine_leak(rendered, context="v2.1 unavailable comparison basis")


def test_v21_old_summary_keeps_legacy_comparison_gap_readable():
    rendered = render_cards([evidence_card("EVIDENCE-OLD-SUMMARY")])
    text = rendered["documentText"]
    top = html_section(rendered["documentHtml"], "signal-comfort")
    assert_true("旧版未提供两侧比较" in top,
                "old v2.0 summary should remain readable and explicitly lack comparison")
    assert_true("相对比较未采纳：旧版未提供两侧比较" not in text,
                "legacy absence should not be framed as a failed new comparison")
    assert_no_machine_leak(rendered, context="v2.1 old comparison gap")


def test_legacy_v20_invalid_if_uses_ambiguous_legacy_condition_label():
    rendered = render_cards([evidence_card("EVIDENCE-OLD-INVALID")])
    next_section = html_section(rendered["documentHtml"], "signal-next-conditions")
    assert_true("旧版失效条件（评审对象未区分）" in next_section,
                "old invalid_if should keep a legacy ambiguous-condition label")
    assert_true("若价格有效突破对应空间约束，本轮判断失效。" in next_section,
                "old invalid_if text should remain visible")
    assert_true("削弱该侧适配" not in next_section,
                "old invalid_if must not be presented as v2.1 fixed-fit weaken conditions")
    assert_true("Put A级" in rendered["indexText"] and "Call C级" in rendered["indexText"],
                "legacy condition wording must not alter old grades")


def test_v21_missing_projection_price_does_not_guess_market_context_price():
    adv = v21_advisory()
    summary = v21_summary(adv, market_status="UNAVAILABLE", price=None, unit="")
    card = evidence_card("EVIDENCE-V21-NO-PRICE", advisory_payload=adv, summary_payload=summary)
    card["market_context"] = {"price": 100000, "quote_currency": "USDT"}
    card["llm_review"]["schema_version"] = "signal_llm_review@2.1.0"
    card["llm_review"]["prompt_version"] = "signal_llm_review_prompt@2.1.0"
    rendered = render_cards([card])
    combined = rendered["indexText"] + " " + html_section(rendered["documentHtml"], "signal-comfort") + " " + rendered["documentText"].split("中文市场事实", 1)[0]
    assert_true("卡时价格未提供" in combined,
                "missing projected market snapshot should be explicit")
    assert_true("100,000 USDT" not in combined and "价格 100,000" not in combined,
                "reader must not backfill with market_context realtime-like price")
    assert_no_machine_leak(rendered, context="v2.1 missing projected price")


def test_v21_projection_hash_mismatch_invalidates_new_summary():
    adv = v21_advisory()
    summary = v21_summary(adv, hash_override="sha256:bad-projection")
    rendered = render_cards([evidence_card("EVIDENCE-V21-BAD-HASH", advisory_payload=adv, summary_payload=summary)])
    text = rendered["documentText"]
    assert_true("发布投影校验未通过" in text,
                "v2.1 summary projection hash mismatch should be visible")
    assert_true("Call 侧相对更有依据" not in html_section(rendered["documentHtml"], "signal-comfort"),
                "invalid projected comparison should not be adopted")
    assert_no_machine_leak(rendered, context="v2.1 projection hash mismatch")


def test_v21_change_gap_mentions_specific_reason_class():
    adv = v21_advisory()
    for fact in adv["market_facts"]:
        if fact.get("id") == "change.context.status":
            fact.update(
                value="变化不可用",
                usable=False,
                summary_cn="前后卡资料结构不同，不能做变化推断。",
                limitations_cn=["当前截面事实仍可单独阅读。"],
            )
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    rendered = render_cards([v21_card("EVIDENCE-V21-CHANGE-GAP", advisory_payload=adv)])
    changes = html_section(rendered["documentHtml"], "signal-key-changes")
    assert_true("前后卡资料结构不同" in changes and "当前截面仍可阅读" in changes,
                "change module should classify the unavailable reason instead of using a generic gap")
    assert_true("查看对照缺口" in changes,
                "change gap should still link to data quality facts")
    assert_no_machine_leak(rendered, context="v2.1 change gap")


def test_v21_change_gap_uses_reader_wording_for_record_source_check():
    adv = v21_advisory()
    for fact in adv["market_facts"]:
        if fact.get("id") == "change.context.status":
            fact.update(
                value="变化不可用",
                usable=False,
                summary_cn="前后记录 hash 缺失，不能做变化推断。",
                limitations_cn=[],
            )
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    rendered = render_cards([v21_card("EVIDENCE-V21-CHANGE-SOURCE", advisory_payload=adv)])
    changes = html_section(rendered["documentHtml"], "signal-key-changes")
    assert_true("前后记录来源未通过核验" in changes,
                "change gap should use reader wording for source-record verification failures")
    assert_true("哈希" not in changes and "hash" not in changes.lower(),
                "change gap should not expose technical hash wording")


def main():
    test_v21_list_header_and_decision_use_projection_price_bias_and_comparison()
    test_legacy_sidebar_headline_localizes_weak_direction_tokens()
    test_v21_evidence_roles_drive_source_groups_without_machine_role_tokens()
    test_v21_invalid_side_comparison_isolated_from_side_grades()
    test_v21_unavailable_comparison_hides_raw_basis()
    test_v21_old_summary_keeps_legacy_comparison_gap_readable()
    test_legacy_v20_invalid_if_uses_ambiguous_legacy_condition_label()
    test_v21_missing_projection_price_does_not_guess_market_context_price()
    test_v21_projection_hash_mismatch_invalidates_new_summary()
    test_v21_change_gap_mentions_specific_reason_class()
    test_v21_change_gap_uses_reader_wording_for_record_source_check()
    print("signal_evidence_v21_frontend: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_evidence_v21_frontend: FAIL - " + str(exc))
        sys.exit(1)
