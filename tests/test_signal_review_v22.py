import copy
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "signal_review_v2.py"
AS_OF_MS = 1788753590501
RECORD_HASH = "sha256:" + "b" * 64


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_review_v2_v22_tests", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clone(value):
    return copy.deepcopy(value)


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def fact(
    fact_id,
    *,
    topic="structure",
    label_cn="空间结构",
    summary_cn="价格处在可核验空间结构内。",
    value=1,
    unit="观察",
    source_group="options",
    observed_at_ms=AS_OF_MS - 1000,
    usable=True,
):
    return {
        "id": fact_id,
        "topic": topic,
        "label_cn": label_cn,
        "value": value,
        "unit": unit,
        "source_refs": ["本卡事实"],
        "source_group": source_group,
        "observed_at_ms": observed_at_ms,
        "window": "当前卡",
        "usable": usable,
        "summary_cn": summary_cn,
        "limitations_cn": [],
        "dependencies": [],
        "provenance": {"method": "unit-test"},
    }


def packet(*, schema="signal_evidence_packet@2.1.0", facts=None):
    return {
        "schema": schema,
        "identity": {
            "card_id": "CARD-V22",
            "symbol": "BTC",
            "strategy_version": "1.6.1",
            "as_of_ms": AS_OF_MS,
            "source_record_hash": RECORD_HASH,
        },
        "facts": list(facts or [
            fact("F_STRUCTURE"),
            fact(
                "F_RESPONSE",
                topic="pressure_response",
                label_cn="压力响应",
                summary_cn="同窗主动成交和价格响应显示不利推进暂未扩张。",
                source_group="price_flow",
            ),
            fact(
                "F_COUNTER",
                topic="adverse_pressure",
                label_cn="主要反证",
                summary_cn="近端仍有一段不利侧推进，需要保留竞争解释。",
                source_group="price_flow",
            ),
        ]),
        "limitations_cn": ["候选报价尚未进入本层评估。"],
    }


def card(
    *,
    direction="NEUTRAL",
    support_label="MODEL_SUPPORT",
    decision_state="MODEL_SUPPORT",
    has_block=False,
    window_active=True,
):
    return {
        "identity": {
            "card_id": "CARD-V22",
            "symbol": "BTC",
            "strategy_version": "1.6.1",
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
            "soft_gates": [{"gate": "WAIT", "reason_cn": "等待窗口确认"}]
            if "WAIT" in support_label else [],
        },
        "signal_window": {
            "is_active": window_active,
            "state": "ACTIVE",
            "nr_state": "ACTIVE",
        },
    }


def role(ref, role_name="supports_fit", claim_cn="该事实支持本侧适配解释。"):
    return {"ref": ref, "role": role_name, "claim_cn": claim_cn}


def mechanism(refs=None, summary_cn="本侧解释较强。"):
    return {"summary_cn": summary_cn, "refs": list(refs or ["F_STRUCTURE"])}


def side_v22(
    grade,
    roles,
    *,
    mechanism_refs=None,
    mechanism_cn="本侧解释较强。",
    primary_counter_ref=None,
    basis_cn="结构和响应事实共同支持该侧环境。",
    alternative_cn="竞争解释仍需跟踪，但尚未覆盖当前论证。",
):
    return {
        "grade": grade,
        "basis_cn": basis_cn,
        "mechanism": mechanism(mechanism_refs, mechanism_cn),
        "primary_counter_ref": primary_counter_ref,
        "alternative_cn": alternative_cn,
        "next_observation_cn": "继续观察不利侧推进是否重新扩大。",
        "strengthen_if_cn": ["结构保持且压力响应继续未扩张。"],
        "weaken_if_cn": ["不利侧推进扩大并越过当前结构。"],
        "evidence_roles": list(roles),
        "unresolved_conditions_cn": ["候选报价与净补偿尚未评估。"],
    }


def price_bias():
    return {
        "bias": "MIXED",
        "basis_cn": "近端响应与较长窗口背景存在分歧。",
        "counter_cn": "单个窗口不能证明全程路径。",
        "invalid_if_cn": "若主动成交和价格响应重新同向，需要更新方向复核。",
        "evidence_refs": ["F_RESPONSE"],
        "counter_evidence_refs": ["F_COUNTER"],
    }


def comparison(relative_side="tie"):
    return {
        "relative_side": relative_side,
        "basis_cn": "两侧证据接近，暂不设优先侧。",
        "evidence_refs": ["F_STRUCTURE", "F_RESPONSE"],
        "flip_if_cn": "若一侧出现更清楚的结构与响应共振，重新比较。",
    }


def guidance(*, outlooks=None, summary_cn="Put 侧相对更值得研究，核心机制是空间结构暂时约束下行侵入；当下主要问题是补偿尚未评估。"):
    default_outlooks = [
        {
            "horizon_hours": 4,
            "scenario_cn": "若近端下行推进继续被结构区间吸收，Put 侧适配解释增强。",
            "watch_cn": "观察同窗主动流和价格响应是否重新向下扩张。",
            "evidence_refs": ["F_RESPONSE"],
        },
        {
            "horizon_hours": 24,
            "scenario_cn": "若背景结构位继续保持，Call 与 Put 的相对比较仍需跟随空间迁移重判。",
            "watch_cn": "观察价格相对锚带和墙位的距离是否明显改变。",
            "evidence_refs": ["F_STRUCTURE"],
        },
    ]
    return {
        "summary_cn": summary_cn,
        "tradeoffs_cn": [
            "如果把卖方结构放得更远，下行侵入风险下降，但需要候选报价确认权利金是否足够。",
            "如果靠近当前结构争取补偿，需要看到空间约束和价格响应继续支持该侧。",
        ],
        "evidence_refs": ["F_STRUCTURE", "F_RESPONSE"],
        "outlooks": default_outlooks if outlooks is None else list(outlooks),
    }


def payload_v22(put_side, call_side, *, guide=None):
    return {
        "side_evidence_ratings": {
            "put_credit": clone(put_side),
            "call_credit": clone(call_side),
        },
        "price_bias": price_bias(),
        "side_comparison": comparison(),
        "advisory_guidance": guide or guidance(),
    }


def side_v21(grade, refs):
    return {
        "grade": grade,
        "basis_cn": "空间约束与压力响应共同支持该侧环境。",
        "mechanism_cn": "空间结构仍能解释本侧不利侵入风险受到约束。",
        "market_counter_cn": "主要反证仍保留。",
        "alternative_cn": "竞争解释是压力传导不足。",
        "next_observation_cn": "继续观察不利侧压力是否穿透当前空间结构。",
        "strengthen_if_cn": ["结构约束保持且不利推进继续受限。"],
        "weaken_if_cn": ["不利推进扩大并穿透当前空间结构。"],
        "evidence_roles": [role(ref) for ref in refs],
        "unresolved_conditions_cn": [],
    }


def payload_v21():
    return {
        "side_evidence_ratings": {
            "put_credit": side_v21("B", ["F_STRUCTURE"]),
            "call_credit": side_v21("C", ["F_STRUCTURE"]),
        },
        "price_bias": price_bias(),
        "side_comparison": {
            "relative_side": "put_credit",
            "basis_cn": "Put 侧相对更有依据。",
            "evidence_refs": ["F_STRUCTURE"],
            "flip_if_cn": "若 Call 侧增强则重做比较。",
        },
    }


def side_v20(grade, refs):
    return {
        "grade": grade,
        "basis_cn": "空间约束与压力响应共同支持该侧环境。",
        "market_counter_cn": "主要反证仍保留。",
        "alternative_cn": "竞争解释是压力传导不足。",
        "next_observation_cn": "继续观察不利侧压力是否穿透当前空间结构。",
        "invalid_if_cn": "若空间结构失效则重新判断。",
        "evidence_refs": list(refs),
        "counter_evidence_refs": [],
        "unresolved_conditions_cn": [],
    }


def payload_v20(*, with_bias=False):
    payload = {
        "side_evidence_ratings": {
            "put_credit": side_v20("B", ["F_STRUCTURE"]),
            "call_credit": side_v20("C", ["F_STRUCTURE"]),
        },
    }
    if with_bias:
        payload["price_bias"] = price_bias()
    return payload


def test_v22_request_schema_contract(tool):
    request = tool.build_request(packet(), tool.DEFAULT_MODEL)
    schema = request["_local_json_schema"]
    side_props = schema["properties"]["side_evidence_ratings"]["properties"]["put_credit"]["properties"]
    prompt = "\n".join(message["content"] for message in request["messages"])
    assert_true(request["_local_prompt_version"] == "signal_llm_review_prompt@2.2.0",
                "latest prompt should be v2.2")
    assert_true("advisory_guidance" in schema["required"],
                "v2.2 schema should require advisory guidance")
    assert_true("mechanism" in side_props and "mechanism_cn" not in side_props,
                "v2.2 side contract should use structured mechanism")
    assert_true("market_counter_cn" not in side_props,
                "model should not repeat derived counter text")
    for phrase in (
        "结构与来源可信度",
        "不利压力",
        "同窗价格响应",
        "4h/12h 较长窗口",
        "A 不要求完美承接",
        "未来压力持续已经被证明",
        "已发生的反对事实、缺少证据、竞争机制和未来条件要分开表达",
        "未知不是反对",
        "相对更值得研究的侧别",
        "关键适配机制和当下主要问题",
        "mechanism 只解释哪项结构、价格响应或传导关系正在限制本侧不利侵入",
        "basis_cn 只说明为什么落在该等级以及主要薄弱处，不要复述 mechanism",
        "evidence_roles.claim_cn 要写具体当前事实对本侧适配的作用",
        "通用提醒集中放在 advisory_guidance 或 unresolved_conditions_cn",
        "避免重复常识",
        "outlooks 可给 4 小时近端情景、24 小时背景情景、两项都给或不给",
        "不要输出泛泛占位情景",
        "哪一侧受到何种影响",
        "什么适配变化会让另一侧相对变优",
    ):
        assert_true(phrase in prompt, "prompt missing v2.2 reasoning duty: " + phrase)


def test_v22_b_wait_guidance_does_not_create_permission(tool):
    review = tool.build_review(
        card(support_label="WAIT", decision_state="WAIT", window_active=False),
        payload_v22(
            side_v22("B", [role("F_STRUCTURE"), role("F_COUNTER", "counters_fit", "近端推进仍是主要反证。")],
                     mechanism_refs=["F_STRUCTURE"], primary_counter_ref="F_COUNTER"),
            side_v22("C", [role("F_STRUCTURE")], mechanism_refs=["F_STRUCTURE"]),
        ),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    put = advisory["side_evidence_ratings"]["put_credit"]
    assert_true(put["grade"] == "B" and put["status"] == "RATED",
                "WAIT should not lower evidence grade")
    assert_true(advisory["local_action_state"]["put_credit"]["state"] == "WAIT",
                "old window boundary should still constrain preparation")
    assert_true(advisory["advisory_guidance"]["status"] == "ASSESSED",
                "B grade may still carry research guidance")
    summary = tool.build_summary(
        review,
        card(support_label="WAIT", decision_state="WAIT", window_active=False),
    )
    assert_true(summary["action_summary_cn"] == advisory["advisory_guidance"]["summary_cn"],
                "new summary should use guidance as the primary list summary")
    assert_true(summary["display_action_summary_cn"] == advisory["advisory_guidance"]["summary_cn"],
                "display summary should also use guidance for v2.2 cards")
    assert_true(summary["source_boundary"] == advisory["source_boundary"],
                "new summary and full review should expose the same source boundary")
    assert_true(summary["review_schema_version"] == "signal_llm_review@2.2.0"
                and summary["review_protocol"] == "2.2"
                and summary["has_advisory_guidance"] is True,
                "summary should expose native v2.2 review identity")
    assert_true("窗口尚未打开" in summary["source_boundary"]["summary_cn"],
                "source boundary should preserve the old window reason")


def test_v22_a_grade_uses_refs_not_mechanism_keywords(tool):
    review = tool.build_review(
        card(),
        payload_v22(
            side_v22("A", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_STRUCTURE"], mechanism_cn="本侧解释较强。"),
            side_v22("B", [role("F_RESPONSE")], mechanism_refs=["F_RESPONSE"]),
        ),
        packet(),
    )
    put = review["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]
    assert_true(put["status"] == "RATED" and put["grade"] == "A",
                "A/S validation should rely on mechanism refs, not Chinese keywords")
    tool.validate_persisted_review(review)


def test_v22_primary_counter_is_derived_and_model_payload_excludes_counter_text(tool):
    review = tool.build_review(
        card(),
        payload_v22(
            side_v22("B", [
                role("F_STRUCTURE"),
                role("F_COUNTER", "counters_fit", "近端仍有不利推进，是本侧主要反证。"),
            ], primary_counter_ref="F_COUNTER"),
            side_v22("C", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    put = review["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]
    assert_true(put["primary_counter_ref"] == "F_COUNTER",
                "primary counter should persist the selected counter ref")
    assert_true(put["market_counter_cn"] == "近端仍有不利推进，是本侧主要反证。",
                "legacy UI counter text should be derived from the selected role")
    model_payload = tool.model_payload_from_review(review)
    model_put = model_payload["side_evidence_ratings"]["put_credit"]
    assert_true("market_counter_cn" not in model_put,
                "recovered model payload should not expose derived counter text")
    assert_true(model_put["primary_counter_ref"] == "F_COUNTER",
                "recovered model payload should keep primary counter ref")


def test_v22_source_boundary_ignores_new_grades(tool):
    neutral_card = card(direction="NEUTRAL")
    watch_review = tool.build_review(
        neutral_card,
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("C", [role("F_RESPONSE")], mechanism_refs=["F_RESPONSE"]),
        ),
        packet(),
    )
    prepare_review = tool.build_review(
        neutral_card,
        payload_v22(
            side_v22("A", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_STRUCTURE"]),
            side_v22("S", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_RESPONSE"]),
        ),
        packet(),
    )
    watch_boundary = watch_review["integrated_trade_advisory"]["source_boundary"]
    prepare_boundary = prepare_review["integrated_trade_advisory"]["source_boundary"]
    assert_true(watch_boundary == prepare_boundary,
                "source boundary should be derived from the source card, not evidence grades")
    text = str(watch_boundary)
    for phrase in ("启动关注", "普通观察", "人工准备", "评级", "B级", "C级", "A级", "S级"):
        assert_true(phrase not in text, "source boundary leaked grade action phrase " + phrase)
    assert_true("原信号未列出额外等待或阻断" in watch_boundary["summary_cn"],
                "no extra source boundary should be explicit")
    assert_true("只读审计记录" in watch_boundary["summary_cn"],
                "read-only execution should be preserved as background")


def test_v22_source_boundary_does_not_activate_inactive_reasons(tool):
    source_card = card(direction="NEUTRAL")
    source_card["blocking"]["has_block"] = False
    source_card["blocking"]["reason_cn"] = "未触发，仅为历史解释"
    source_card["blocking"]["block_reason_cn"] = "历史硬风险未触发"
    source_card["factor_cross_section"] = {
        "macro_pressure": {
            "macro_shock": {
                "block": False,
                "reason_cn": "宏观阻断未触发",
                "reason": {"reason_cn": "宏观嵌套阻断未触发"},
            }
        }
    }
    review = tool.build_review(
        source_card,
        payload_v22(
            side_v22("A", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_STRUCTURE"]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    boundary = review["integrated_trade_advisory"]["source_boundary"]
    text = str(boundary)
    assert_true("本地硬风险" not in text and "未触发" not in text,
                "inactive reasons must not create a source hard-risk boundary")
    assert_true(boundary["sides"]["put_credit"]["state"] == "INFO"
                and boundary["sides"]["call_credit"]["state"] == "INFO",
                "inactive hard-risk reasons should leave source boundary informational")
    assert_true(review["integrated_trade_advisory"]["local_action_state"]["put_credit"]["state"] == "PREPARE",
                "source-boundary fix must not change old local action calculation")


def test_v22_source_boundary_ignores_inactive_soft_gates_but_keeps_old_action(tool):
    source_card = card(direction="NEUTRAL")
    source_card["blocking"]["soft_gates"] = [{
        "gate": "WAIT",
        "active": False,
        "reason_cn": "等待未触发，仅为历史解释",
    }]
    review = tool.build_review(
        source_card,
        payload_v22(
            side_v22("A", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_STRUCTURE"]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    boundary = review["integrated_trade_advisory"]["source_boundary"]
    assert_true("等待未触发" not in str(boundary),
                "inactive soft gate reason should not be displayed as a wait boundary")
    assert_true(boundary["sides"]["put_credit"]["state"] == "INFO"
                and "原信号未列出额外等待或阻断" in boundary["summary_cn"],
                "inactive soft gate alone should not display source waiting")
    assert_true(review["integrated_trade_advisory"]["local_action_state"]["put_credit"]["state"] == "WAIT",
                "old local action state should remain driven by the legacy soft_gates context")


def test_v22_source_boundary_keeps_hard_risk_visible(tool):
    blocked_card = card(direction="NEUTRAL", has_block=True)
    review = tool.build_review(
        blocked_card,
        payload_v22(
            side_v22("A", [role("F_STRUCTURE"), role("F_RESPONSE")],
                     mechanism_refs=["F_STRUCTURE"]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    boundary = review["integrated_trade_advisory"]["source_boundary"]
    summary = tool.build_summary(review, blocked_card)
    assert_true("测试阻断" in boundary["summary_cn"],
                "hard risk reason should be visible in source boundary")
    assert_true(summary["source_boundary"] == boundary,
                "summary should expose the same source boundary as full review")
    assert_true(boundary["sides"]["put_credit"]["state"] == "BLOCKED"
                and boundary["sides"]["call_credit"]["state"] == "BLOCKED",
                "hard risk should mark both source-boundary sides as blocked")


def test_v22_source_boundary_keeps_active_macro_hard_risk_visible(tool):
    blocked_card = card(direction="NEUTRAL")
    blocked_card["factor_cross_section"] = {
        "macro_pressure": {
            "macro_shock": {
                "block": True,
                "reason_cn": "宏观硬风险触发",
            }
        }
    }
    review = tool.build_review(
        blocked_card,
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    boundary = review["integrated_trade_advisory"]["source_boundary"]
    assert_true("宏观硬风险触发" in boundary["summary_cn"],
                "active macro hard risk should be visible")
    assert_true(boundary["sides"]["put_credit"]["state"] == "BLOCKED",
                "active macro hard risk should block source boundary")


def test_v22_actual_fixed_soft_gate_shape_is_window_wait_not_hard_risk(tool):
    fixed_card = card(direction="NEUTRAL", support_label="MODEL_SUPPORT")
    fixed_card["blocking"] = {
        "block_kind": "SOFT_GATE",
        "hard_veto": None,
        "has_block": True,
        "soft_gates": [{
            "gate": "WINDOW_NOT_OPEN",
            "reason_cn": "DIE+Anchor 时序窗口未开，方向仅作观察预热",
        }],
    }
    fixed_card["decision_matrix"]["decision_state"] = "WAIT_CONFIRMATION"
    fixed_card["signal_window"]["is_active"] = False
    review = tool.build_review(
        fixed_card,
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    boundary = advisory["source_boundary"]
    text = str(boundary)
    assert_true("本地硬风险" not in text,
                "SOFT_GATE with has_block=true must not become source hard risk")
    assert_true(boundary["sides"]["put_credit"]["state"] == "WAIT"
                and boundary["sides"]["call_credit"]["state"] == "WAIT",
                "actual fixed soft-gate shape should display source waiting")
    assert_true("窗口尚未打开" in boundary["summary_cn"],
                "closed source window should remain visible")
    assert_true(advisory["local_action_state"]["put_credit"]["state"] == "BLOCKED",
                "old local action state should remain unchanged for has_block summary")


def test_v22_unknown_has_block_type_is_not_hard_risk(tool):
    unknown_card = card(direction="NEUTRAL")
    unknown_card["blocking"] = {
        "has_block": True,
        "hard_veto": None,
        "soft_gates": [],
    }
    review = tool.build_review(
        unknown_card,
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("B", [role("F_STRUCTURE")]),
        ),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    boundary = advisory["source_boundary"]
    assert_true("本地硬风险" not in str(boundary),
                "has_block alone should not be rendered as hard risk")
    assert_true("阻断类型未明" in boundary["summary_cn"],
                "unknown has_block type should be disclosed for manual boundary review")
    assert_true(boundary["sides"]["put_credit"]["state"] == "WAIT",
                "unknown has_block type should not claim no source boundary")
    assert_true(advisory["local_action_state"]["put_credit"]["state"] == "BLOCKED",
                "legacy action should keep using the old has_block summary")


def test_v22_source_boundary_without_card_says_source_unavailable(tool):
    boundary = tool._source_boundary({}, {}, card=None)
    assert_true("源卡资料未提供" in boundary["summary_cn"],
                "missing source card should not assume no extra boundary")
    assert_true("未列出额外等待或阻断" not in boundary["summary_cn"],
                "missing source card should not claim no source boundary")
    assert_true(boundary["sides"]["put_credit"]["label_cn"] == "资料未提供",
                "missing source card side labels should be explicit")


def test_v22_guidance_outlooks_allow_zero_one_or_two_items(tool):
    two_outlooks = guidance()["outlooks"]
    for outlooks in ([], two_outlooks[:1], two_outlooks):
        review = tool.build_review(
            card(),
            payload_v22(
                side_v22("B", [role("F_STRUCTURE")]),
                side_v22("B", [role("F_RESPONSE")], mechanism_refs=["F_RESPONSE"]),
                guide=guidance(outlooks=outlooks),
            ),
            packet(),
        )
        advisory = review["integrated_trade_advisory"]
        assert_true(advisory["advisory_guidance"]["status"] == "ASSESSED",
                    "0/1/2 valid outlook items should all be accepted")
        assert_true(len(advisory["advisory_guidance"]["outlooks"]) == len(outlooks),
                    "accepted outlook count should match the model output")
        assert_true(review["status"] == "OK",
                    "optional outlook omission should not make the review partial")
        tool.validate_persisted_review(review)


def test_v22_guidance_bad_outlook_is_local_and_hash_bound(tool):
    duplicate_outlooks = guidance()["outlooks"][:1] + [clone(guidance()["outlooks"][0])]
    review = tool.build_review(
        card(),
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("B", [role("F_RESPONSE")], mechanism_refs=["F_RESPONSE"]),
            guide=guidance(outlooks=duplicate_outlooks),
        ),
        packet(),
    )
    advisory = review["integrated_trade_advisory"]
    assert_true(advisory["side_evidence_ratings"]["put_credit"]["grade"] == "B",
                "bad guidance should not erase valid side grades")
    assert_true(advisory["advisory_guidance"]["status"] == "UNAVAILABLE",
                "duplicate guidance outlook should be isolated as unavailable")
    assert_true(review["status"] == "PARTIAL",
                "bad guidance should make the review partial")
    summary = tool.build_summary(review)
    assert_true(summary["assessment_hash"] == advisory["validation"]["assessment_hash"],
                "summary should bind the same assessment hash")
    projection = clone(summary)
    projection_hash = projection.pop("display_projection_hash")
    expected = "sha256:" + tool.hashlib.sha256(
        tool._browser_canonical_json(projection).encode("utf-8")
    ).hexdigest()
    assert_true(projection_hash == expected,
                "display projection hash should bind guidance and source boundary")

    unknown_outlooks = guidance()["outlooks"][:1] + [{
        "horizon_hours": 12,
        "scenario_cn": "若未知窗口条件变化，暂不应纳入本卡建议。",
        "watch_cn": "等待有效窗口事实后再判断。",
        "evidence_refs": ["F_STRUCTURE"],
    }]
    unknown_review = tool.build_review(
        card(),
        payload_v22(
            side_v22("B", [role("F_STRUCTURE")]),
            side_v22("B", [role("F_RESPONSE")], mechanism_refs=["F_RESPONSE"]),
            guide=guidance(outlooks=unknown_outlooks),
        ),
        packet(),
    )
    assert_true(unknown_review["integrated_trade_advisory"]["advisory_guidance"]["status"] == "UNAVAILABLE",
                "unknown outlook horizon should remain a local guidance failure")
    assert_true(unknown_review["integrated_trade_advisory"]["side_evidence_ratings"]["call_credit"]["grade"] == "B",
                "bad guidance horizon should not erase legal side ratings")


def test_v22_old_packet_and_v21_review_stay_readable(tool):
    p = packet(schema="signal_evidence_packet@2.0.0")
    review = tool.build_review(
        card(),
        payload_v21(),
        p,
        prompt_version=tool.PROMPT_VERSION_2_1_0,
    )
    assert_true(review["schema_version"] == "signal_llm_review@2.1.0",
                "v2.1 prompt should still persist v2.1 output")
    assert_true(review["evidence_context"]["schema"] == "signal_evidence_packet@2.0.0",
                "old packet schema should be preserved in persisted context")
    assert_true(tool.supported_review_protocol(review) == "2.1",
                "v2.1 reviews should remain a distinct supported protocol")
    assert_true("mechanism_cn" in tool.model_side_fields_for_review(review),
                "v2.1 side field set should stay readable")
    summary = tool.build_summary(review)
    assert_true(summary["review_schema_version"] == "signal_llm_review@2.1.0"
                and summary["review_protocol"] == "2.1"
                and summary["has_advisory_guidance"] is False,
                "v2.1 summaries should not masquerade as native v2.2 guidance")
    assert_true("advisory_guidance" not in summary,
                "legacy summaries should not invent v2.2 guidance")

    error = tool.build_error_review(
        card(),
        p,
        "冻结旧请求失败，保留旧输入包。",
        prompt_version=tool.PROMPT_VERSION,
    )
    assert_true(error["evidence_context"]["schema"] == "signal_evidence_packet@2.0.0",
                "frozen old packet should be allowed for a v2.2 error review")
    assert_true(error["integrated_trade_advisory"]["advisory_guidance"]["status"] == "UNAVAILABLE",
                "error review should carry unavailable guidance without resetting protocol")


def test_v22_legacy_v20_and_v201_reviews_stay_on_old_contract(tool):
    p = packet(schema="signal_evidence_packet@2.0.0")
    legacy = tool.build_review(
        card(),
        payload_v20(),
        p,
        prompt_version=tool.LEGACY_PROMPT_VERSION,
    )
    assert_true(legacy["schema_version"] == "signal_llm_review@2.0.0",
                "v2.0 prompt should persist the v2.0 review schema")
    assert_true(tool.supported_review_protocol(legacy) == "2.0",
                "v2.0 prompt should remain a supported legacy protocol")
    assert_true("side_comparison" not in legacy["integrated_trade_advisory"],
                "v2.0 reviews should not gain side comparison")
    assert_true("advisory_guidance" not in legacy["integrated_trade_advisory"],
                "v2.0 reviews should not gain v2.2 guidance")
    tool.validate_persisted_review(legacy)
    legacy_summary = tool.build_summary(legacy)
    assert_true(legacy_summary["review_schema_version"] == "signal_llm_review@2.0.0"
                and legacy_summary["review_protocol"] == "2.0"
                and legacy_summary["has_advisory_guidance"] is False,
                "v2.0 summaries should carry explicit legacy identity")

    legacy_with_bias = tool.build_review(
        card(),
        payload_v20(with_bias=True),
        p,
        prompt_version=tool.PROMPT_VERSION_2_0_1,
    )
    assert_true(legacy_with_bias["integrated_trade_advisory"]["price_bias"]["status"] == "ASSESSED",
                "v2.0.1 price bias branch should stay readable")
    assert_true("invalid_if_cn" in tool.model_side_fields_for_review(legacy_with_bias),
                "v2.0 side field set should stay on the old invalid_if contract")


def main():
    tool = load_tool()
    test_v22_request_schema_contract(tool)
    test_v22_b_wait_guidance_does_not_create_permission(tool)
    test_v22_a_grade_uses_refs_not_mechanism_keywords(tool)
    test_v22_primary_counter_is_derived_and_model_payload_excludes_counter_text(tool)
    test_v22_source_boundary_ignores_new_grades(tool)
    test_v22_source_boundary_does_not_activate_inactive_reasons(tool)
    test_v22_source_boundary_ignores_inactive_soft_gates_but_keeps_old_action(tool)
    test_v22_source_boundary_keeps_hard_risk_visible(tool)
    test_v22_source_boundary_keeps_active_macro_hard_risk_visible(tool)
    test_v22_actual_fixed_soft_gate_shape_is_window_wait_not_hard_risk(tool)
    test_v22_unknown_has_block_type_is_not_hard_risk(tool)
    test_v22_source_boundary_without_card_says_source_unavailable(tool)
    test_v22_guidance_outlooks_allow_zero_one_or_two_items(tool)
    test_v22_guidance_bad_outlook_is_local_and_hash_bound(tool)
    test_v22_old_packet_and_v21_review_stay_readable(tool)
    test_v22_legacy_v20_and_v201_reviews_stay_on_old_contract(tool)


if __name__ == "__main__":
    main()
