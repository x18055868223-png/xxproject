import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "signal_review_v2.py"
AS_OF_MS = 1788753590501
RECORD_HASH = "sha256:" + "a" * 64


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_review_v2_for_tests", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def clone(value):
    return copy.deepcopy(value)


def fact(
    fact_id,
    *,
    topic="structure",
    label_cn="空间结构",
    summary_cn="价格处在可核验空间结构内。",
    source_group="期权结构",
    observed_at_ms=AS_OF_MS - 1000,
    usable=True,
):
    return {
        "id": fact_id,
        "topic": topic,
        "label_cn": label_cn,
        "value": 1,
        "unit": "观察",
        "source_refs": ["本卡事实"],
        "source_group": source_group,
        "observed_at_ms": observed_at_ms,
        "window": "当前卡",
        "usable": usable,
        "summary_cn": summary_cn,
        "limitations_cn": [],
        "dependencies": [],
    }


def packet(card_id="CARD-V2", facts=None):
    return {
        "schema": "signal_evidence_packet@2.0.0",
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "strategy_version": "1.6.0",
            "as_of_ms": AS_OF_MS,
            "source_record_hash": RECORD_HASH,
        },
        "facts": list(facts or [
            fact("F_STRUCTURE"),
            fact(
                "F_PRESSURE",
                topic="pressure_response",
                label_cn="不利侧压力响应",
                summary_cn="主动成交压力与价格表现共同说明不利侧侵入暂未扩张。",
                source_group="量价结构",
            ),
        ]),
        "limitations_cn": ["候选报价尚未进入本层评估。"],
    }


def card(
    card_id="CARD-V2",
    *,
    direction="BULLISH",
    support_label="MODEL_SUPPORT",
    decision_state="MODEL_SUPPORT",
    has_block=False,
    window_active=True,
    window_state="ACTIVE",
):
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "strategy_version": "1.6.0",
            "confirmed_time_ms": AS_OF_MS,
        },
        "producer_integrity": {"record_hash": RECORD_HASH},
        "decision": {
            "lean": direction,
            "support_label": support_label,
            "trade_allowed": False,
        },
        "decision_matrix": {
            "direction": direction,
            "decision_state": decision_state,
            "execution_allowed": False,
        },
        "blocking": {
            "has_block": has_block,
            "hard_veto": {"reason_cn": "测试阻断"} if has_block else None,
            "soft_gates": [{"gate": "WAIT"}] if "WAIT" in support_label else [],
        },
        "signal_window": {
            "is_active": window_active,
            "state": window_state,
            "nr_state": window_state,
        },
    }


def side(
    grade,
    refs,
    *,
    basis_cn="空间约束与不利侧压力响应共同支持该侧环境。",
    counter_cn="主要反证仍保留，但尚未覆盖当前结构解释。",
    alternative_cn="竞争解释是压力传导不足，需要继续核验。",
    counter_refs=None,
    unresolved=None,
):
    return {
        "grade": grade,
        "basis_cn": basis_cn,
        "market_counter_cn": counter_cn,
        "alternative_cn": alternative_cn,
        "next_observation_cn": "继续观察不利侧压力是否穿透当前空间结构。",
        "invalid_if_cn": "若空间结构迁移或不利侧压力有效扩张，需要重新评级。",
        "evidence_refs": list(refs),
        "counter_evidence_refs": list(counter_refs or []),
        "unresolved_conditions_cn": list(unresolved or []),
    }


def payload(put_side, call_side):
    return {
        "side_evidence_ratings": {
            "put_credit": put_side,
            "call_credit": call_side,
        }
    }


def price_bias(
    bias="BULLISH",
    refs=None,
    *,
    basis_cn="主动成交压力与价格响应显示价格偏多，但仍需结合空间约束核验。",
    counter_cn="主要反证是价格响应可能来自短暂流动性扰动。",
    invalid_if_cn="若主动成交压力回落或价格响应转弱，需要重新判断方向。",
    counter_refs=None,
):
    return {
        "bias": bias,
        "basis_cn": basis_cn,
        "counter_cn": counter_cn,
        "invalid_if_cn": invalid_if_cn,
        "evidence_refs": list(refs or ["F_PRESSURE"]),
        "counter_evidence_refs": list(counter_refs or []),
    }


def payload_with_bias(put_side, call_side, bias=None):
    value = payload(put_side, call_side)
    value["price_bias"] = bias or price_bias()
    return value


def test_request_contract_and_prompt(tool):
    request = tool.build_request(packet(), "model-x")
    text = "\n".join(item["content"] for item in request["messages"])
    assert_true(request["model"] == "model-x", "request should preserve model")
    assert_true(request["_local_prompt_version"] == tool.PROMPT_VERSION,
                "prompt version should be v2")
    assert_true(request["_local_review_mode"] == "single_evidence_v2",
                "review mode should be single evidence v2")
    assert_true(request["response_format"] == {"type": "json_object"},
                "DeepSeek JSON mode request should be explicit")
    assert_true(request["thinking"] == {"type": "enabled"}
                and request["reasoning_effort"] == "high",
                "normal v2 request should use high thinking")
    assert_true("temperature" not in request and "top_p" not in request,
                "thinking requests should not carry sampling knobs")
    for phrase in ("空间约束", "不利侧侵入", "竞争解释", "价格方向", "定性贝叶斯", "胜率"):
        assert_true(phrase in text, "prompt missing " + phrase)
    assert_true("future_24h" not in text and "blind" not in text.lower(),
                "v2 prompt should not require legacy blind or 24h report")
    schema = request["_local_json_schema"]
    assert_true(set(schema["required"]) == {"side_evidence_ratings", "price_bias"},
                "new request schema should require price bias")
    assert_true(set(schema["properties"]["side_evidence_ratings"][
        "properties"]) == {"put_credit", "call_credit"},
        "schema should expose only two sides")
    recovery = tool.build_request(packet(), "model-x", recovery=True)
    assert_true("recovery" in recovery["_local_call_profile"],
                "recovery flag should be visible to runtime")
    assert_true(recovery["thinking"] == {"type": "disabled"}
                and "reasoning_effort" not in recovery,
                "recovery request should use non-thinking delivery")


def test_review_summary_hash_context_and_revalidation(tool):
    p = packet()
    c = card()
    review = tool.build_review(
        c,
        payload_with_bias(
            side("A", ["F_STRUCTURE", "F_PRESSURE"]),
            side("C", ["F_STRUCTURE"]),
        ),
        p,
        model="model-x",
        reviewed_at="2026-09-08T00:00:00+08:00",
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(review["schema_version"] == "signal_llm_review@2.0.0",
                "review schema should be v2")
    assert_true("facts" not in review["evidence_context"],
                "evidence_context must not duplicate facts")
    assert_true(advisory["market_facts"] == p["facts"],
                "market facts should preserve packet facts")
    assert_true(advisory["side_evidence_ratings"]["put_credit"]["grade"] == "A",
                "valid A should survive")
    assert_true(advisory["price_bias"]["schema"] == "price_bias@1.0.0",
                "new advisory should carry price bias schema")
    assert_true(advisory["price_bias"]["status"] == "ASSESSED"
                and advisory["price_bias"]["bias"] == "BULLISH",
                "valid price bias should survive")
    assert_true(advisory["local_action_state"]["put_credit"]["state"] == "PREPARE",
                "valid put A on bullish card should prepare")
    summary = tool.build_summary(review)
    assert_true(summary["schema"] == "signal_evidence_summary@2.0.0",
                "summary schema should be v2")
    assert_true(summary["assessment_hash"] == advisory["validation"]["assessment_hash"],
                "summary should bind full advisory hash")
    assert_true(summary["price_bias"]["bias"] == "BULLISH",
                "summary should carry compact price bias")
    assert_true(tool.validate_persisted_review(review)["ok"] is True,
                "persisted review should validate")
    assert_true(tool.revalidate_review(c, review)["local_action_state_ok"] is True,
                "source-card revalidation should recompute local action")

    tampered = clone(review)
    tampered["integrated_trade_advisory"]["action_summary_cn"] = "被篡改的摘要"
    try:
        tool.build_summary(tampered)
    except tool.EvidenceFormatError:
        pass
    else:
        raise AssertionError("tampered advisory should fail assessment hash validation")


def test_legacy_payload_without_price_bias_stays_readable(tool):
    review = tool.build_review(
        card(),
        payload(side("A", ["F_STRUCTURE", "F_PRESSURE"]), side("C", ["F_STRUCTURE"])),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(review["prompt_version"] == tool.LEGACY_PROMPT_VERSION,
                "missing price bias should be preserved as old prompt payload")
    assert_true("price_bias" not in advisory,
                "old payload should not be backfilled with invented direction")
    assert_true(tool.validate_persisted_review(review)["ok"] is True,
                "old prompt review should remain strictly readable")


def test_required_price_bias_missing_is_local_gap(tool):
    review = tool.build_review(
        card(),
        payload(side("A", ["F_STRUCTURE", "F_PRESSURE"]), side("C", ["F_STRUCTURE"])),
        packet(),
        require_price_bias=True,
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(review["prompt_version"] == tool.PROMPT_VERSION,
                "runtime-required missing direction should use new prompt identity")
    assert_true(review["status"] == "PARTIAL",
                "missing price bias should make review partial without dropping sides")
    assert_true(advisory["side_evidence_ratings"]["put_credit"]["grade"] == "A",
                "side rating should survive missing direction object")
    assert_true(advisory["price_bias"]["status"] == "UNAVAILABLE"
                and advisory["price_bias"]["bias"] == "UNDETERMINED",
                "missing price bias should be unavailable")
    assert_true(tool.validate_persisted_review(review)["ok"] is True,
                "required missing price bias should persist as a valid local gap")
    tampered = clone(review)
    tampered["status"] = "OK"
    tampered["integrated_trade_advisory"]["validation"]["status"] = "OK"
    tampered["integrated_trade_advisory"]["validation"]["assessment_hash"] = None
    tampered["integrated_trade_advisory"]["validation"]["assessment_hash"] = tool._assessment_hash(
        tampered["integrated_trade_advisory"]
    )
    try:
        tool.validate_persisted_review(tampered)
    except tool.EvidenceFormatError:
        pass
    else:
        raise AssertionError("unavailable price bias should not validate as OK")


def test_price_bias_needs_pressure_or_response_not_only_structure(tool):
    p = packet(facts=[fact("F_STRUCTURE")])
    answer = payload_with_bias(
        side("C", ["F_STRUCTURE"]),
        side("C", ["F_STRUCTURE"]),
        price_bias(
            refs=["F_STRUCTURE"],
            basis_cn="现价仍在空间结构内，因此价格偏多。",
            counter_cn="主要反证是墙位距离本身不能证明方向。",
            invalid_if_cn="若空间结构迁移，需要重新判断。",
        ),
    )
    review = tool.build_review(card(direction="NEUTRAL"), answer, p)
    bias = review["integrated_trade_advisory"]["price_bias"]
    assert_true(bias["status"] == "UNAVAILABLE",
                "price bias cannot be assessed from structure-only refs")
    assert_true(review["integrated_trade_advisory"]["side_evidence_ratings"][
        "put_credit"]["grade"] == "C",
        "invalid price bias should not damage side evidence ratings")


def test_price_bias_future_ref_is_local_gap(tool):
    p = packet(facts=[
        fact("F_STRUCTURE"),
        fact("F_PRESSURE"),
        fact(
            "F_FUTURE_PRESSURE",
            topic="pressure_response",
            label_cn="未来压力响应",
            summary_cn="未来主动成交压力与价格表现。",
            source_group="量价结构",
            observed_at_ms=AS_OF_MS + 1,
        ),
    ])
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload_with_bias(
            side("B", ["F_STRUCTURE", "F_PRESSURE"]),
            side("C", ["F_STRUCTURE"]),
            price_bias(
                "BULLISH",
                ["F_FUTURE_PRESSURE"],
                basis_cn="主动成交压力与价格响应显示价格偏多。",
                counter_cn="主要反证是方向事实仍需核验。",
                invalid_if_cn="若压力响应转弱，需要重新判断方向。",
            ),
        ),
        p,
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(advisory["price_bias"]["status"] == "UNAVAILABLE",
                "future price-bias ref should be unavailable")
    assert_true(advisory["side_evidence_ratings"]["put_credit"]["grade"] == "B",
                "future price-bias ref should not damage side ratings")


def test_wait_block_and_readonly_do_not_lower_evidence_grade(tool):
    p = packet()
    wait_review = tool.build_review(
        card(support_label="WAIT_CONFIRMATION", decision_state="WAIT_CONFIRMATION"),
        payload(side("A", ["F_STRUCTURE", "F_PRESSURE"]), side("C", ["F_STRUCTURE"])),
        p,
    )
    put = wait_review["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]
    action = wait_review["integrated_trade_advisory"]["local_action_state"]["put_credit"]
    assert_true(put["grade"] == "A" and put["status"] == "RATED",
                "WAIT should not cap the evidence letter")
    assert_true(action["state"] == "WAIT", "WAIT should remain in local action state")

    block_review = tool.build_review(
        card(has_block=True),
        payload(side("S", ["F_STRUCTURE", "F_PRESSURE"]), side("C", ["F_STRUCTURE"])),
        p,
    )
    block_put = block_review["integrated_trade_advisory"][
        "side_evidence_ratings"]["put_credit"]
    block_action = block_review["integrated_trade_advisory"][
        "local_action_state"]["put_credit"]
    assert_true(block_put["grade"] == "S", "BLOCK should not lower evidence grade")
    assert_true(block_action["state"] == "BLOCKED", "BLOCK should stop action locally")


def test_invalid_future_and_unusable_refs_are_side_local(tool):
    p = packet(facts=[
        fact("F_STRUCTURE"),
        fact("F_FUTURE", observed_at_ms=AS_OF_MS + 1),
        fact("F_UNUSABLE", usable=False, label_cn="主动买卖流"),
    ])
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(
            side("A", ["F_NOT_REAL"], basis_cn="空间结构支持该侧环境。"),
            side("B", ["F_STRUCTURE"]),
        ),
        p,
    )
    ratings = review["integrated_trade_advisory"]["side_evidence_ratings"]
    assert_true(ratings["put_credit"]["status"] == "UNRATED"
                and ratings["put_credit"]["grade"] is None,
                "forged ref should only unrate that side")
    assert_true(ratings["put_credit"]["evidence_refs"] == [],
                "invalid refs should not survive in displayable side")
    assert_true(ratings["call_credit"]["status"] == "RATED"
                and ratings["call_credit"]["grade"] == "B",
                "other side should keep valid observation")

    future_review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(side("B", ["F_FUTURE"]), side("B", ["F_UNUSABLE"])),
        p,
    )
    future_ratings = future_review["integrated_trade_advisory"]["side_evidence_ratings"]
    assert_true(future_ratings["put_credit"]["status"] == "UNRATED",
                "future fact should not be current evidence")
    assert_true(future_ratings["call_credit"]["status"] == "UNRATED",
                "unusable fact should not be cited as evidence")


def test_pure_direction_cannot_be_a_or_s_but_can_start_b_attention(tool):
    momentum_fact = fact(
        "F_MOMENTUM",
        topic="direction",
        label_cn="方向压力",
        summary_cn="主动成交偏向上方。",
        source_group="量价方向",
    )
    p = packet(facts=[momentum_fact])
    pure_direction = "主动成交偏向上方，方向动量较强。"
    a_review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(
            side(
                "A",
                ["F_MOMENTUM"],
                basis_cn=pure_direction,
                counter_cn="主要反证是主动成交可能回落。",
                alternative_cn="竞争解释是主动成交可能回落。",
            ),
            side(
                "C",
                ["F_MOMENTUM"],
                basis_cn=pure_direction,
                counter_cn="主要反证是主动成交可能回落。",
                alternative_cn="竞争解释是主动成交可能回落。",
            ),
        ),
        p,
    )
    assert_true(a_review["integrated_trade_advisory"]["side_evidence_ratings"][
        "put_credit"]["status"] == "UNRATED",
        "pure direction should not be accepted as A")

    b_review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(
            side(
                "B",
                ["F_MOMENTUM"],
                basis_cn=pure_direction,
                counter_cn="主要反证是主动成交可能回落。",
                alternative_cn="竞争解释是主动成交可能回落。",
            ),
            side(
                "C",
                ["F_MOMENTUM"],
                basis_cn=pure_direction,
                counter_cn="主要反证是主动成交可能回落。",
                alternative_cn="竞争解释是主动成交可能回落。",
            ),
        ),
        p,
    )
    assert_true(b_review["integrated_trade_advisory"]["side_evidence_ratings"][
        "put_credit"]["grade"] == "B",
        "B may start attention from incomplete but useful evidence")


def test_s_does_not_require_extra_source(tool):
    single_source_packet = packet(facts=[
        fact("F_SPACE", summary_cn="空间约束、墙位距离和压力响应共同支持该侧。")
    ])
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(side("S", ["F_SPACE"]), side("C", ["F_SPACE"])),
        single_source_packet,
    )
    assert_true(review["integrated_trade_advisory"]["side_evidence_ratings"][
        "put_credit"]["grade"] == "S",
        "S should be available from a strong relation in current facts")


def test_distance_percent_text_is_not_probability_rejected(tool):
    p = packet(facts=[
        fact("F_SPACE", summary_cn="现价距离上方墙约 2.4%，空间约束仍可核验。")
    ])
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(
            side("A", ["F_SPACE"], basis_cn="现价距离上方墙约 2.4%，空间约束仍可核验。"),
            side("C", ["F_SPACE"], basis_cn="现价距离上方墙约 2.4%，普通观察。"),
        ),
        p,
    )
    assert_true(review["integrated_trade_advisory"]["side_evidence_ratings"][
        "put_credit"]["grade"] == "A",
        "distance percent should not be treated as a forbidden probability")


def test_bad_top_shape_raises_but_bad_side_isolated(tool):
    try:
        tool.build_review(card(), {"summary_cn": "多余", "side_evidence_ratings": {}}, packet())
    except tool.EvidenceFormatError:
        pass
    else:
        raise AssertionError("bad top shape should raise EvidenceFormatError")

    bad_side = side("A", ["F_STRUCTURE"])
    bad_side.pop("alternative_cn")
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(bad_side, side("B", ["F_STRUCTURE"])),
        packet(),
    )
    ratings = review["integrated_trade_advisory"]["side_evidence_ratings"]
    assert_true(ratings["put_credit"]["status"] == "UNRATED",
                "bad side shape should be side-local")
    assert_true(ratings["call_credit"]["grade"] == "B",
                "valid side should survive bad peer side")


def test_unsafe_human_text_and_error_review_are_sanitized(tool):
    unsafe = side(
        "A",
        ["F_STRUCTURE"],
        basis_cn=(
            "factor_cross_section.anchor 显示 70% 胜率，"
            "卖出具体行权价即可成交。"
        ),
    )
    review = tool.build_review(card(direction="NEUTRAL"), payload(unsafe, side("B", ["F_STRUCTURE"])), packet())
    put = review["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]
    text = json.dumps(put, ensure_ascii=False)
    assert_true(put["status"] == "UNRATED", "unsafe side text should unrate side")
    for forbidden in ("factor_cross_section", "胜率", "行权价"):
        assert_true(forbidden not in text, "unsafe text leaked " + forbidden)

    error = tool.build_error_review(
        card(),
        packet(),
        "HTTP 返回为空，等待下一次综合评审。",
        reviewed_at="2026-09-08T00:00:00+08:00",
    )
    assert_true(error["status"] == "ERROR", "error review should be ERROR")
    assert_true(tool.build_summary(error)["assessment_hash"] == error[
        "integrated_trade_advisory"]["validation"]["assessment_hash"],
        "error review should still publish a consistent hash")
    assert_true(tool.revalidate_review(card(), error)["source_identity_ok"] is True,
                "error review should revalidate source identity and action")


def test_neutral_two_sided_prepare_summary_does_not_imply_dual_trade(tool):
    review = tool.build_review(
        card(direction="NEUTRAL"),
        payload(
            side("A", ["F_STRUCTURE", "F_PRESSURE"]),
            side("A", ["F_STRUCTURE", "F_PRESSURE"]),
        ),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(advisory["local_action_state"]["put_credit"]["state"] == "PREPARE",
                "neutral put side can be reviewed independently")
    assert_true(advisory["local_action_state"]["call_credit"]["state"] == "PREPARE",
                "neutral call side can be reviewed independently")
    assert_true("不代表双侧同时交易" in advisory["action_summary_cn"],
                "summary must not imply dual-side trading")


def main():
    tool = load_tool()
    test_request_contract_and_prompt(tool)
    test_review_summary_hash_context_and_revalidation(tool)
    test_legacy_payload_without_price_bias_stays_readable(tool)
    test_required_price_bias_missing_is_local_gap(tool)
    test_price_bias_needs_pressure_or_response_not_only_structure(tool)
    test_price_bias_future_ref_is_local_gap(tool)
    test_wait_block_and_readonly_do_not_lower_evidence_grade(tool)
    test_invalid_future_and_unusable_refs_are_side_local(tool)
    test_pure_direction_cannot_be_a_or_s_but_can_start_b_attention(tool)
    test_s_does_not_require_extra_source(tool)
    test_distance_percent_text_is_not_probability_rejected(tool)
    test_bad_top_shape_raises_but_bad_side_isolated(tool)
    test_unsafe_human_text_and_error_review_are_sanitized(tool)
    test_neutral_two_sided_prepare_summary_does_not_imply_dual_trade(tool)
    print("signal_review_v2: PASS")


if __name__ == "__main__":
    main()
