import copy
import hashlib
from html.parser import HTMLParser
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_signal_comfort_frontend import (
    assert_no_machine_leak,
    assert_true,
    fixture_valid_as,
    render_cards,
)


AS_OF_MS = 1781751600000


def canonical_payload(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assessment_hash(advisory):
    payload = copy.deepcopy(advisory)
    payload.setdefault("validation", {}).pop("assessment_hash", None)
    return "sha256:" + hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def side(grade, *, basis, counter, alternative, refs, counter_refs=None, next_observation=None, invalid_if=None):
    return {
        "status": "RATED" if grade else "UNRATED",
        "grade": grade,
        "basis_cn": basis,
        "market_counter_cn": counter,
        "alternative_cn": alternative,
        "next_observation_cn": next_observation or "继续观察价格是否仍停留在对应空间约束内。",
        "invalid_if_cn": invalid_if or "若价格有效突破对应空间约束，本轮判断失效。",
        "evidence_refs": list(refs),
        "counter_evidence_refs": list(counter_refs or []),
        "unresolved_conditions_cn": [],
        "validation_reasons_cn": [],
    }


def market_fact(
    fact_id,
    topic,
    label,
    value,
    *,
    unit="",
    source_group,
    source_refs,
    summary,
    limitations=None,
    dependencies=None,
    window="current",
    usable=True,
):
    return {
        "id": fact_id,
        "topic": topic,
        "label_cn": label,
        "value": value,
        "unit": unit,
        "source_refs": list(source_refs),
        "source_group": source_group,
        "observed_at_ms": AS_OF_MS,
        "window": window,
        "usable": usable,
        "summary_cn": summary,
        "limitations_cn": list(limitations or []),
        "dependencies": list(dependencies or []),
    }


def update_market_fact(advisory_payload, fact_id, **updates):
    for item in advisory_payload["market_facts"]:
        if item.get("id") == fact_id:
            item.update(updates)
            return item
    raise AssertionError("missing fixture fact: " + fact_id)


def market_facts():
    return [
        market_fact(
            "market.price.current",
            "structure_position",
            "当前价格",
            79538.07,
            unit="USDT",
            source_refs=["market_context.price"],
            source_group="PRICE",
            summary="当前价格作为墙位距离的共同基准，不代表可成交期权报价。",
        ),
        market_fact(
            "structure.gamma.call_wall",
            "structure_position",
            "上方 Call 墙",
            80000,
            unit="USDT",
            source_refs=["factor_cross_section.gex_info"],
            source_group="GEX",
            summary="上方 Call 墙用于观察上行边界是否被检验。",
            limitations=["墙位不是不可突破边界。"],
        ),
        market_fact(
            "structure.distance.call_wall_pct",
            "structure_position",
            "现价距上方 Call 墙",
            0.5808,
            unit="%",
            source_refs=["factor_cross_section.gex_info"],
            source_group="GEX",
            summary="向上触及边界所需价格移动较小。",
            limitations=["距离比较不代表墙的承接强度。"],
        ),
        market_fact(
            "structure.gamma.put_wall",
            "structure_position",
            "下方 Put 墙",
            78500,
            unit="USDT",
            source_refs=["factor_cross_section.gex_info"],
            source_group="GEX",
            summary="下方 Put 墙用于观察下行空间是否仍被约束。",
            limitations=["墙位不是不可突破边界。"],
        ),
        market_fact(
            "structure.distance.put_wall_pct",
            "structure_position",
            "现价距下方 Put 墙",
            1.3051,
            unit="%",
            source_refs=["factor_cross_section.gex_info"],
            source_group="GEX",
            summary="下方空间相对更宽，但宽度本身不等于交易准入。",
            limitations=["距离比较不代表墙的承接强度。"],
        ),
        market_fact(
            "structure.gamma.regime",
            "structure_position",
            "Gamma 结构状态",
            "Gamma 过渡区",
            source_refs=["factor_cross_section.gamma_regime"],
            source_group="GAMMA_REGIME",
            summary="Gamma 处于过渡区，不能只凭净 Gamma 正值确认波动收缩。",
            limitations=["过渡区只说明体制未明，不单独构成方向依据。"],
        ),
        market_fact(
            "structure.gex.net_gamma_notional_usd",
            "structure_position",
            "净 Gamma 名义规模",
            243837180.38,
            unit="USD",
            source_refs=["factor_cross_section.gex_info"],
            source_group="GEX",
            summary="净 Gamma 为正，但在过渡体制里只作为背景解释。",
            limitations=["同属期权结构来源，不能与墙位、翻转点重复计为多份独立确认。"],
        ),
        market_fact(
            "structure.anchor.score",
            "structure_anchor",
            "价格锚来源状态",
            57.8,
            unit="分",
            source_refs=["factor_cross_section.anchor"],
            source_group="ANCHOR",
            summary="旧锚偏离合成贴合刻度，不用于来源质量判断。",
        ),
        market_fact(
            "pressure.tmv.direction",
            "adverse_pressure",
            "量价主干方向",
            "下行",
            source_refs=["factor_cross_section.tmvf"],
            source_group="TMV",
            window="30m",
            summary="量价主干给出下行压力，但必须结合空间约束判断是否适配。",
        ),
        market_fact(
            "response.flow_price.relation",
            "price_pressure_response",
            "压力与价格响应",
            "传导受阻",
            source_refs=["factor_cross_section.tmvf", "factor_cross_section.micro_flow"],
            source_group="TMV",
            window="30m",
            summary="不利侧推进暂未形成高效突破。",
            dependencies=["pressure.tmv.direction", "pressure.cvd.combined_direction"],
        ),
        market_fact(
            "pressure.cvd.combined_direction",
            "active_flow",
            "主动买卖流",
            "分歧",
            source_refs=["factor_cross_section.micro_flow"],
            source_group="FLOW",
            window="4h",
            summary="主动成交与价格响应没有形成同向强推进。",
            limitations=["主动流与价格来源存在重叠，不能重复加票。"],
        ),
        market_fact(
            "change.context.status",
            "change_context",
            "变化核验",
            "变化可用",
            source_refs=["transition_context"],
            source_group="QUALITY",
            window="previous_card",
            summary="前后卡身份和时间顺序匹配，变化事实可用于本次阅读。",
        ),
    ]


def advisory(put_grade="A", call_grade="C", *, put_refs=None, action_summary=None):
    payload = {
        "side_evidence_ratings": {
            "put_credit": side(
                put_grade,
                basis="下方空间约束仍有效，价格下行推进受阻，Put 信用价差具备较强信号层适配依据。",
                counter="价格仍在锚带内；候选报价缺失，净补偿稍后确认。",
                alternative="主动成交分歧可能说明压力尚未完成传导。",
                refs=put_refs or ["structure.distance.put_wall_pct", "response.flow_price.relation"],
                counter_refs=["pressure.cvd.combined_direction"],
            ),
            "call_credit": side(
                call_grade,
                basis="上方空间也可读，但当前支持优势不足。",
                counter="若买方主动流重新增强，Call 侧反证会加重。",
                alternative="市场可能处于宽幅整理而非明确上方受限。",
                refs=["structure.distance.call_wall_pct"],
                counter_refs=["pressure.cvd.combined_direction"],
            ),
        },
        "local_action_state": {
            "put_credit": {
                "state": "PREPARE" if put_grade in ("A", "S") else "WATCH",
                "label_cn": "可进入人工准备" if put_grade in ("A", "S") else "普通观察",
                "reasons_cn": ["旧等待/阻断边界未否决该侧人工准备。"] if put_grade in ("A", "S") else [],
            },
            "call_credit": {
                "state": "WATCH",
                "label_cn": "普通观察",
                "reasons_cn": ["证据优势不足，先观察。"],
            },
        },
        "action_summary_cn": action_summary or "Put 侧证据等级 A，可以进入末日垂直价差的人工交易准备；Call 侧普通观察。",
        "market_facts": market_facts(),
        "quote_boundary_cn": "候选两腿、报价、费用、净补偿与退出条件仍在交易准备环节确认。",
        "validation": {"side_errors": []},
    }
    payload["validation"]["assessment_hash"] = assessment_hash(payload)
    return payload


def evidence_summary(advisory_payload, *, override_hash=None):
    ratings = advisory_payload["side_evidence_ratings"]
    actions = advisory_payload["local_action_state"]
    return {
        "schema_version": "signal_evidence_summary@2.0.0",
        "put_credit": {
            "grade": ratings["put_credit"]["grade"],
            "status": ratings["put_credit"]["status"],
            "basis_cn": ratings["put_credit"]["basis_cn"],
        },
        "call_credit": {
            "grade": ratings["call_credit"]["grade"],
            "status": ratings["call_credit"]["status"],
            "basis_cn": ratings["call_credit"]["basis_cn"],
        },
        "local_action_state": actions,
        "action_summary_cn": advisory_payload["action_summary_cn"],
        "as_of_ms": AS_OF_MS,
        "input_packet_hash": "packet-hash-hidden-in-reader",
        "assessment_hash": override_hash or advisory_payload["validation"]["assessment_hash"],
    }


def evidence_card(card_id="EVIDENCE-A", *, advisory_payload=None, summary_payload=None):
    advisory_payload = advisory_payload or advisory()
    return {
        "schema": {"name": "signal_review_card", "version": "1.0.0", "status": "FINAL"},
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-4:],
            "symbol": "BTC",
            "strategy_name": "Astra 总体证据评级测试",
            "strategy_version": "2.0.0",
            "confirmed_at": "2026-06-19T11:00:00+08:00",
        },
        "market_context": {"price": 100000, "quote_currency": "USDT"},
        "quality": {"overall": "OK", "all_required_sources_ready": True},
        "decision": {
            "lean": "BULLISH_STRONG",
            "support_label": "TRADE_SUPPORT_REVIEW",
            "confidence": 91,
        },
        "decision_matrix": {"support_label": "TRADE_SUPPORT_REVIEW", "execution_allowed": False},
        "reasoning": {
            "summary_cn": "旧证据账本不应在 v2 新卡并排显示。",
            "evidence": [{"key": "RAW_SHOULD_NOT_RENDER", "source_ref": "factor_cross_section.hidden"}],
        },
        "llm_review": {
            "status": "OK",
            "schema_version": "signal_llm_review@2.0.0",
            "prompt_version": "signal_llm_review_prompt@2.0.0",
            "review_mode": "single_evidence_v2",
            "input_packet_hash": "packet-hash-hidden-in-reader",
            "content": {
                "summary_cn": "旧 LLM summary 不应在 v2 新卡显示。",
                "main_supporting_factors": ["旧支持列表不应显示。"],
                "future_24h_bayesian_report": {"report_cn": "未来 24 小时旧长报告不应显示。"},
                "integrated_trade_advisory": advisory_payload,
            },
        },
        "signal_evidence_summary": summary_payload if summary_payload is not None else evidence_summary(advisory_payload),
        "display_layers": {"headline": "v2 evidence card"},
    }


def evidence_card_with_spatial_overrides(card_id, **overrides):
    adv = advisory()
    mapping = {
        "price": "market.price.current",
        "call_wall": "structure.gamma.call_wall",
        "call_distance": "structure.distance.call_wall_pct",
        "put_wall": "structure.gamma.put_wall",
        "put_distance": "structure.distance.put_wall_pct",
        "regime": "structure.gamma.regime",
        "gamma": "structure.gex.net_gamma_notional_usd",
        "tmv": "pressure.tmv.direction",
        "response": "response.flow_price.relation",
    }
    for key, value in overrides.items():
        fact_id = mapping[key]
        if isinstance(value, dict):
            update_market_fact(adv, fact_id, **value)
        else:
            update_market_fact(adv, fact_id, value=value)
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card(card_id, advisory_payload=adv)


def first_comfort_panel_class(html):
    marker = '<div class="comfort-panel '
    start = html.find(marker)
    assert_true(start >= 0, "rendered document should include a comfort panel")
    class_start = html.find('class="', start) + len('class="')
    class_end = html.find('"', class_start)
    return html[class_start:class_end]


class MarketFactVisibilityParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details_depth = 0
        self.visible_items = 0
        self.details_items = 0
        self.div_depth = 0
        self.item_stack = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        classes = set(attr_map.get("class", "").split())
        if tag == "details":
            self.details_depth += 1
        if tag == "div":
            self.div_depth += 1
        if "market-fact-overview-item" in classes:
            self.item_stack.append({"depth": self.div_depth, "in_details": bool(self.details_depth), "text": ""})
            if self.details_depth:
                self.details_items += 1
            else:
                self.visible_items += 1

    def handle_endtag(self, tag):
        if tag == "div":
            if self.item_stack and self.item_stack[-1]["depth"] == self.div_depth:
                self.items.append(self.item_stack.pop())
            self.div_depth -= 1
        if tag == "details" and self.details_depth:
            self.details_depth -= 1

    def handle_data(self, data):
        for item in self.item_stack:
            item["text"] += data


def html_section(html, section_id):
    marker = f'id="{section_id}"'
    start = html.find(marker)
    assert_true(start >= 0, "rendered html should include section: " + section_id)
    if section_id.startswith("market-") and section_id != "market-evidence":
        end = html.find('<article id="market-', start + len(marker))
        if end < 0:
            end = html.find('<section class="section"', start + len(marker))
    else:
        end = html.find('<section class="section"', start + len(marker))
    return html[start:end if end >= 0 else len(html)]


def assert_section_order(html, section_ids, message):
    positions = []
    for section_id in section_ids:
        marker = f'id="{section_id}"'
        pos = html.find(marker)
        assert_true(pos >= 0, "missing ordered section: " + section_id)
        positions.append(pos)
    assert_true(positions == sorted(positions), message)

def evidence_card_with_many_facts():
    adv = advisory()
    for idx in range(34):
        adv["market_facts"].append({
            "id": f"F_EXTRA_{idx}",
            "topic": "verified_change",
            "label_cn": f"补充事实 {idx + 1}",
            "value": f"观察 {idx + 1}",
            "unit": "",
            "source_refs": ["transition_context"],
            "source_group": "QUALITY",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": f"第 {idx + 1} 条补充事实保留在主读区。",
            "limitations_cn": [],
            "dependencies": [],
        })
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card("EVIDENCE-MANY-FACTS", advisory_payload=adv)


def evidence_card_with_topic_edges():
    adv = advisory()
    adv["market_facts"].extend([
        {
            "id": "F_PRICE_FROM_FLOW",
            "topic": "price_response",
            "label_cn": "价格响应来自主动流窗口",
            "value": "传导受阻",
            "unit": "",
            "source_refs": ["factor_cross_section.micro_flow"],
            "source_group": "FLOW",
            "observed_at_ms": AS_OF_MS,
            "window": "30m",
            "usable": True,
            "summary_cn": "虽然来源含主动流，本项说明的是价格响应，应归入价格表现。",
            "limitations_cn": ["OHLC 只能作为区间代理，不能证明路径先后。"],
            "dependencies": [],
        },
        {
            "id": "F_ACTIVE_PRESSURE_FROM_TMV",
            "topic": "adverse_pressure",
            "label_cn": "量价主干压力",
            "value": "压力分歧",
            "unit": "",
            "source_refs": ["factor_cross_section.tmvf"],
            "source_group": "TMV",
            "observed_at_ms": AS_OF_MS,
            "window": "4h",
            "usable": True,
            "summary_cn": "本项来自量价主干，不能当作主动成交确认。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "F_CVD_PRESSURE",
            "topic": "adverse_pressure",
            "label_cn": "四小时主动买卖流",
            "value": -0.12,
            "unit": "",
            "source_refs": ["factor_cross_section.micro_flow"],
            "source_group": "PRICE_FLOW",
            "observed_at_ms": AS_OF_MS,
            "window": "4h",
            "usable": True,
            "summary_cn": "本项仍是主动成交压力，价格与流联合来源名不能把它改成价格路径。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "F_OPTION_SKEW_PRESSURE",
            "topic": "adverse_pressure",
            "label_cn": "期权偏斜方向",
            "value": -0.63,
            "unit": "",
            "source_refs": ["factor_cross_section.skew"],
            "source_group": "OPTIONS_STRUCTURE",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "期权偏斜是期权侧背景，不能视作主动成交确认。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "F_STALE_UNKNOWN",
            "topic": "source_freshness",
            "label_cn": "陈旧来源缺口",
            "value": "待确认",
            "unit": "",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "QUALITY",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": False,
            "summary_cn": "来源时效不足时，只影响依赖该来源的判断。",
            "limitations_cn": ["陈旧事实不能冒充当前约束。"],
            "dependencies": [],
        },
    ])
    adv["side_evidence_ratings"]["put_credit"]["evidence_refs"] = [
        "structure.distance.put_wall_pct",
        "response.flow_price.relation",
        "F_PRICE_FROM_FLOW",
    ]
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card("EVIDENCE-TOPIC-EDGES", advisory_payload=adv)


def evidence_card_with_sorted_structure_facts():
    adv = advisory()
    adv["market_facts"] = [
        {
            "id": "structure.distance.call_wall_pct",
            "topic": "structure_position",
            "label_cn": "现价距上方 Call 墙",
            "value": 2.81,
            "unit": "%",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "GEX",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "现价距上方 Call 墙为 2.81%。",
            "limitations_cn": ["墙位不是不可突破边界。"],
            "dependencies": [],
        },
        {
            "id": "structure.gamma.call_wall",
            "topic": "structure_position",
            "label_cn": "上方 Call 墙",
            "value": 103000,
            "unit": "",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "GEX",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "上方 Call 墙为 103000。",
            "limitations_cn": ["墙位不是不可突破边界。"],
            "dependencies": [],
        },
        {
            "id": "market.price.current",
            "topic": "structure_position",
            "label_cn": "当前价格",
            "value": 100000,
            "unit": "",
            "source_refs": ["market_context.price"],
            "source_group": "PRICE",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "当前价格为 100000。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "structure.gamma.put_wall",
            "topic": "structure_position",
            "label_cn": "下方 Put 墙",
            "value": 97000,
            "unit": "",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "GEX",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "下方 Put 墙为 97000。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "structure.distance.put_wall_pct",
            "topic": "structure_position",
            "label_cn": "现价距下方 Put 墙",
            "value": 3.0,
            "unit": "%",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "GEX",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": True,
            "summary_cn": "现价距下方 Put 墙为 3%。",
            "limitations_cn": [],
            "dependencies": [],
        },
    ]
    adv["side_evidence_ratings"]["put_credit"]["evidence_refs"] = ["market.price.current", "structure.distance.call_wall_pct"]
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card("EVIDENCE-SORTED-STRUCTURE", advisory_payload=adv)


def evidence_card_with_zero_and_missing_facts():
    adv = advisory()
    adv["market_facts"].extend([
        {
            "id": "F_ZERO_PRESSURE",
            "topic": "price_response",
            "label_cn": "零值压力读数",
            "value": 0,
            "unit": "%",
            "source_refs": ["factor_cross_section.tmvf"],
            "source_group": "TMV",
            "observed_at_ms": AS_OF_MS,
            "window": "30m",
            "usable": True,
            "summary_cn": "零值是有效事实，不应被当成缺失隐藏。",
            "limitations_cn": [],
            "dependencies": [],
        },
        {
            "id": "F_MISSING_VISIBLE",
            "topic": "source_freshness",
            "label_cn": "缺失但需阅读的事实",
            "value": None,
            "unit": "",
            "source_refs": ["factor_cross_section.gex_info"],
            "source_group": "QUALITY",
            "observed_at_ms": AS_OF_MS,
            "window": "current",
            "usable": False,
            "summary_cn": "缺失本身是评级缺口，不能从普通页面消失。",
            "limitations_cn": ["必要来源缺失，本项暂不可用于评级。"],
            "dependencies": [],
        },
    ])
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card("EVIDENCE-ZERO-MISSING", advisory_payload=adv)


def evidence_card_with_verified_delta():
    adv = advisory()
    adv["market_facts"].append({
        "id": "F_PRICE_DELTA",
        "topic": "verified_change",
        "label_cn": "现价变化",
        "value": -1.25,
        "unit": "%",
        "source_refs": ["transition_context"],
        "source_group": "QUALITY",
        "observed_at_ms": AS_OF_MS,
        "window": "previous_card",
        "usable": True,
        "summary_cn": "较前一卡下移，只说明已核验差值，不证明盘中路径先后。",
        "limitations_cn": ["只有差值，没有前值和后值；不能反推完整变化过程。"],
        "dependencies": [],
    })
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    return evidence_card("EVIDENCE-DELTA", advisory_payload=adv)


def evidence_card_with_unusable_change_status():
    adv = advisory()
    adv["market_facts"] = [
        fact for fact in adv["market_facts"]
        if fact.get("id") != "change.context.status"
    ]
    adv["market_facts"].append({
        "id": "change.context.status",
        "topic": "change_context",
        "label_cn": "变化核验状态",
        "value": "变化不可用",
        "unit": "",
        "source_refs": ["transition_context"],
        "source_group": "QUALITY",
        "observed_at_ms": AS_OF_MS,
        "window": "previous_card",
        "usable": False,
        "summary_cn": "前后卡身份或时序未通过核验，只能展示缺口，不能生成过程。",
        "limitations_cn": ["变化过程不可造；当前截面事实仍可单独阅读。"],
        "dependencies": [],
    })
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    card = evidence_card("EVIDENCE-CHANGE-BLOCKED", advisory_payload=adv)
    card["transition_context"] = {
        "raw_trap": "RAW_TRANSITION_TRAP_SHOULD_NOT_RENDER",
        "fake_previous": "伪造前值不应显示",
        "fake_current": "伪造后值不应显示",
    }
    return card


def py_review_packet(card_id="PYGEN-EVIDENCE"):
    return {
        "schema": "signal_evidence_packet@2.0.0",
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "strategy_version": "2.0.0",
            "as_of_ms": AS_OF_MS,
            "source_record_hash": "sha256:source-record-pygen",
        },
        "facts": [
            {
                "id": "py_space",
                "topic": "structure_position",
                "label_cn": "空间约束",
                "value": 1.0,
                "unit": "%",
                "source_refs": ["价格锚", "期权墙"],
                "source_group": "空间结构",
                "observed_at_ms": AS_OF_MS,
                "window": "当前截面",
                "usable": True,
                "summary_cn": "现价仍在关键空间约束内，先看边界再看倾向。",
                "limitations_cn": ["墙位不是不可突破边界。"],
                "dependencies": [],
            },
            {
                "id": "py_response",
                "topic": "price_response",
                "label_cn": "价格表现",
                "value": 1e-7,
                "unit": "ratio",
                "source_refs": ["量价结构"],
                "source_group": "价格响应",
                "observed_at_ms": AS_OF_MS,
                "window": "三十分钟",
                "usable": True,
                "summary_cn": "下行推进没有形成高效穿透，Put 侧先进入人工准备更顺。",
                "limitations_cn": [],
                "dependencies": ["py_space"],
            },
            {
                "id": "py_flow",
                "topic": "adverse_pressure",
                "label_cn": "主动成交",
                "value": "存在分歧",
                "unit": "",
                "source_refs": ["主动买卖流"],
                "source_group": "成交流",
                "observed_at_ms": AS_OF_MS,
                "window": "四小时",
                "usable": True,
                "summary_cn": "主动成交没有给出单边强确认，仍是主要竞争解释。",
                "limitations_cn": ["主动流与价格响应存在来源重叠，不能重复加票。"],
                "dependencies": [],
            },
            {
                "id": "py_funding",
                "topic": "funding_rate",
                "label_cn": "资金费率",
                "value": 0.000023,
                "unit": "decimal",
                "source_refs": ["资金费率"],
                "source_group": "FUNDING",
                "observed_at_ms": AS_OF_MS,
                "window": "3d",
                "usable": True,
                "summary_cn": "资金费率为轻微正值，只作为杠杆拥挤背景。",
                "limitations_cn": ["记录提供来源年龄，但 v2 不自行创造新的新鲜度阈值。"],
                "dependencies": [],
            },
            {
                "id": "py_anchor_gap",
                "topic": "structure_position",
                "label_cn": "锚带缺口",
                "value": "显式宽度缺失",
                "unit": "",
                "source_refs": ["价格锚"],
                "source_group": "ANCHOR",
                "observed_at_ms": AS_OF_MS,
                "window": 3,
                "usable": True,
                "summary_cn": "价格锚可作为背景，但锚带宽需要显式给出。",
                "limitations_cn": ["只记录显式带宽；不采用旧代码的默认 0.4% 带宽。"],
                "dependencies": [],
            },
        ],
        "limitations_cn": ["本包只服务信号环境评审，不评价候选报价。"],
    }


def py_review_model_payload():
    return {
        "price_bias": {
            "bias": "MIXED",
            "basis_cn": "价格响应与主动成交尚有分歧，当前多空解释均需保留。",
            "counter_cn": "若某一侧推进增强且结构参照被穿过，分歧解释会减弱。",
            "invalid_if_cn": "结构变化与主动成交形成明确同向响应时重新判断。",
            "evidence_refs": ["py_response", "py_flow"],
            "counter_evidence_refs": ["py_space"],
        },
        "side_evidence_ratings": {
            "put_credit": {
                "grade": "A",
                "basis_cn": "空间约束仍有效，不利推进受阻，Put 侧适配解释更有依据。",
                "market_counter_cn": "主动成交仍有分歧，说明压力传导尚未完全清除。",
                "alternative_cn": "也可能只是宽幅整理，空间约束需要继续被价格表现确认。",
                "next_observation_cn": "继续观察价格是否保持在空间约束内，且不利推进仍然受阻。",
                "invalid_if_cn": "若价格有效穿过空间约束，或主动成交重新形成强单边推进，本侧判断失效。",
                "evidence_refs": ["py_space", "py_response"],
                "counter_evidence_refs": ["py_flow"],
                "unresolved_conditions_cn": ["候选补偿仍需在人工准备环节确认。"],
            },
            "call_credit": {
                "grade": "C",
                "basis_cn": "上方结构可读，但当前没有明显支持优势。",
                "market_counter_cn": "若上行压力恢复，Call 侧竞争解释会加重。",
                "alternative_cn": "当前更像边界内整理，不能单靠空间背景推高等级。",
                "next_observation_cn": "观察上方空间约束是否继续限制价格表现。",
                "invalid_if_cn": "若上行推进穿过空间约束，本侧判断失效。",
                "evidence_refs": ["py_space"],
                "counter_evidence_refs": ["py_flow"],
                "unresolved_conditions_cn": [],
            },
        }
    }


def py_generated_review_card():
    from tools.signal_review_v2 import build_review, build_summary

    card_id = "PYGEN-EVIDENCE"
    source_card = {
        "schema": {"name": "signal_review_card", "version": "1.0.0", "status": "FINAL"},
        "identity": {
            "card_id": card_id,
            "short_id": "PYG1",
            "symbol": "BTC",
            "strategy_name": "Astra Python 集成样本",
            "strategy_version": "2.0.0",
            "confirmed_at": "2026-06-19T11:00:00+08:00",
            "as_of_ms": AS_OF_MS,
            "source_record_hash": "sha256:source-record-pygen",
        },
        "market_context": {"price": "100000", "quote_currency": "USDT"},
        "quality": {"overall": "OK", "all_required_sources_ready": True},
        "decision": {
            "lean": "BULLISH_LEAN",
            "support_label": "TRADE_SUPPORT_REVIEW",
        },
        "decision_matrix": {
            "direction": "BULLISH_LEAN",
            "decision_state": "READY",
            "support_label": "TRADE_SUPPORT_REVIEW",
            "execution_allowed": False,
        },
    }
    packet = py_review_packet(card_id)
    review = build_review(
        source_card,
        py_review_model_payload(),
        packet,
        reviewed_at="2026-06-19T03:00:00Z",
        prompt_version="signal_llm_review_prompt@2.0.1",
    )
    summary = build_summary(review)
    full = copy.deepcopy(source_card)
    full["llm_review"] = copy.deepcopy(review)
    full["llm_review"]["content"] = {"integrated_trade_advisory": review["integrated_trade_advisory"]}
    full["signal_evidence_summary"] = summary
    return full, review, summary


def test_v2_reader_uses_single_evidence_surface_without_legacy_layers():
    card = evidence_card()
    rendered = render_cards([card])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    assert_true("最高辅助交易决策" in text and "Put 信用价差" in text and "Call 信用价差" in text,
                "v2 reader should show the decision module and both side ratings")
    assert_true("Put 侧证据等级 A" in text and "A级" in text and "C级" in text,
                "v2 reader should display side evidence grades")
    assert_true('class="evidence-reader"' in html and 'aria-label="本卡阅读导航"' in html,
                "v2 detail should use the polished reader shell and local navigation")
    assert_section_order(
        html,
        [
            "signal-comfort",
            "signal-spatial-dynamics",
            "signal-key-changes",
            "signal-llm-review",
            "signal-next-conditions",
            "market-evidence",
        ],
        "v2 reader should follow the report order requested by the trader",
    )
    for href in ("#signal-comfort", "#signal-spatial-dynamics", "#signal-key-changes", "#signal-llm-review", "#market-evidence", "#signal-next-conditions"):
        assert_true(f'href="{href}"' in html, "reader nav should link actual section: " + href)
    assert_true("空间约束动力学" in text and "空间分布" in text and "波动机制" in text and "压力检验" in text,
                "spatial dynamics should expose the structure-pressure-response chain")
    spatial = html_section(html, "signal-spatial-dynamics")
    assert_true("过渡区：波动反馈未定" in spatial and "现价更靠近 Call 墙" in spatial,
                "spatial dynamics should lead with feedback and nearest boundary")
    assert_true("距上方 Call 墙约 0.58%" in spatial and "距下方 Put 墙约 1.31%" in spatial,
                "spatial dynamics should summarize wall-distance geometry before raw values")
    assert_true("净 Gamma +$243.84M" in spatial and "约束强度尚未明确" in spatial,
                "spatial dynamics should keep net gamma as context under a transition regime")
    assert_true("量价主干下行" in spatial and "不利侧推进暂未形成高效突破" in spatial,
                "spatial dynamics should summarize pressure and response in the same chain")
    assert_true("Gamma/GEX 当前显示" not in spatial and "现价相对显式锚带" not in spatial,
                "spatial dynamics should not fall back to low-information raw summaries")
    assert_true("关键变化骨架" in text and "LLM独立复核意见" in text.replace(" ", "") and "中文市场事实" in text,
                "v2 reader should keep the four required report modules")
    assert_true("行动状态：可进入人工准备" in text and "行动状态：普通观察" in text,
                "local action should remain visible")
    assert_true("支持来源" in text and "反对来源" in text,
                "source links should distinguish support and opposition sources")
    assert_true("价格仍在锚带内" in text, "market counter-evidence should remain visible")
    assert_true("价格仍在锚带内；候选报价缺失，净补偿稍后确认。" in text,
                "v2 should preserve the accepted counter explanation verbatim even when it mentions quotes")
    assert_true("中文市场事实" in text and "空间约束" in text and "价格响应" in text,
                "v2 market facts should be readable in Chinese")
    assert_true("价格锚来源状态" not in text and "57.8" not in text,
                "legacy anchor fit score should not be shown as market source quality")
    assert_true(text.find("下一观察条件") < text.find("完整审计资料"),
                "v2 reader should put next conditions before the download section")
    assert_true(html.find('id="signal-comfort"') < html.find('id="signal-spatial-dynamics"') < html.find('id="market-evidence"'),
                "v2 reader should reduce information density from decision to mechanism to numeric facts")
    for token in ("全局总等级", "综合最终等级", "整体最终等级"):
        assert_true(token not in text, "v2 reader should not create a global grade layer: " + token)
    for token in ("方向:", "当前限制:", "数据质量", "接管窗口", "系统边界与阻断", "观察身份", "市场价格 100000"):
        assert_true(token not in text, "v2 reader should not show legacy header or metric strip token: " + token)
    assert_true("窗口 current" not in text and "30m" not in text and "4h" not in text and "当前截面" in text and "30 分钟" in text and "4 小时" in text,
                "market fact windows should not leak raw window codes")
    for token in ("既有深入分析", "LLM 深入分析", "证据账本摘要", "未来 24 小时", "旧综合建议", "旧 LLM summary"):
        assert_true(token not in text, "v2 reader should not render legacy layer: " + token)
    for token in ("side_evidence_ratings", "local_action_state", "input_packet_hash", "assessment_hash", "schema_version", "F_SPACE", "structure.distance.call_wall_pct"):
        assert_true(token not in text and token not in html, "v2 reader should not expose machine token: " + token)
    assert_no_machine_leak(rendered, context="v2 evidence reader")


def test_v2_spatial_dynamics_keeps_chinese_transition_regime_undecided():
    rendered = render_cards([evidence_card()])
    spatial = html_section(rendered["documentHtml"], "signal-spatial-dynamics")
    assert_true("过渡区：波动反馈未定" in spatial,
                "Chinese Gamma transition regime should produce an undecided feedback headline")
    assert_true("净 Gamma 为正，提供缓冲背景" in spatial,
                "positive net Gamma should remain a background explanation inside transition")
    assert_true("波动反馈偏抑制" not in spatial and "波动反馈偏放大" not in spatial,
                "transition regime must not be upgraded to a directional feedback conclusion")


def test_v2_spatial_dynamics_reports_english_gamma_regime_conflict():
    card = evidence_card_with_spatial_overrides(
        "EVIDENCE-GAMMA-CONFLICT",
        regime="NEGATIVE_GAMMA",
        gamma=250000000,
    )
    spatial = html_section(render_cards([card])["documentHtml"], "signal-spatial-dynamics")
    assert_true("Gamma 反馈存在分歧" in spatial,
                "English Gamma regime conflict should be visible in the headline")
    assert_true("净 Gamma 符号与体制判断相反" in spatial,
                "conflicting sign and regime should not be silently resolved")
    assert_true("波动反馈偏抑制" not in spatial and "波动反馈偏放大" not in spatial,
                "conflict should not show a one-sided feedback conclusion")


def test_v2_spatial_feedback_is_mirrored_without_changing_grades():
    for regime, gamma, expected in (
        ("正 Gamma 钉住", 250000000, "波动反馈偏抑制"),
        ("负 Gamma 放大", -250000000, "波动反馈偏放大"),
        ("POSITIVE_GAMMA_PINNING", 250000000, "波动反馈偏抑制"),
        ("NEGATIVE_GAMMA_AMPLIFYING", -250000000, "波动反馈偏放大"),
    ):
        rendered = render_cards([evidence_card_with_spatial_overrides(
            "EVIDENCE-FEEDBACK-MIRROR", regime=regime, gamma=gamma)])
        assert_true(expected in html_section(rendered["documentHtml"], "signal-spatial-dynamics"),
                    "explicit Chinese and English regimes should share the same feedback semantics")
        top = html_section(rendered["documentHtml"], "signal-comfort")
        assert_true("A级" in top and "C级" in top,
                    "spatial explanation must not recalculate the accepted side grades")


def test_v2_spatial_dynamics_does_not_infer_feedback_from_net_gamma_without_regime():
    card = evidence_card_with_spatial_overrides(
        "EVIDENCE-GAMMA-NO-REGIME",
        regime={"value": "Gamma 过渡区", "usable": False},
        gamma=500000000,
    )
    spatial = html_section(render_cards([card])["documentHtml"], "signal-spatial-dynamics")
    assert_true("波动反馈待确认" in spatial,
                "missing usable regime should keep feedback undecided even when net gamma is large")
    assert_true("仅凭名义净值不能确认平抑或放大" in spatial,
                "net Gamma alone should not become a volatility feedback conclusion")
    assert_true("波动反馈偏抑制" not in spatial and "波动反馈偏放大" not in spatial,
                "missing regime must not infer volatility direction")


def test_v2_spatial_dynamics_distinguishes_inside_nearest_wall_and_outside_wall():
    inside = html_section(render_cards([evidence_card()])["documentHtml"], "signal-spatial-dynamics")
    outside = html_section(render_cards([evidence_card_with_spatial_overrides(
        "EVIDENCE-OUTSIDE-CALL",
        price=80500,
        call_distance=-0.625,
        put_distance=2.548,
    )])["documentHtml"], "signal-spatial-dynamics")
    assert_true("现价更靠近 Call 墙" in inside and "下方空间更宽" in inside,
                "inside-wall geometry should show the nearer wall and opposite-side room")
    assert_true("现价已越过 Call 墙" in outside and "不能再用“双墙内”解释区间约束" in outside,
                "outside-wall geometry should invalidate the simple inside-band explanation")
    put_inside = html_section(render_cards([evidence_card_with_spatial_overrides(
        "EVIDENCE-NEAR-PUT", price=79000, call_distance=1.266, put_distance=0.633,
    )])["documentHtml"], "signal-spatial-dynamics")
    put_outside = html_section(render_cards([evidence_card_with_spatial_overrides(
        "EVIDENCE-OUTSIDE-PUT", price=78000, call_distance=2.564, put_distance=-0.641,
    )])["documentHtml"], "signal-spatial-dynamics")
    assert_true("现价更靠近 Put 墙" in put_inside and "上方空间更宽" in put_inside,
                "Put geometry should mirror Call without implying stronger support")
    assert_true("现价已越过 Put 墙" in put_outside,
                "lower-wall breaches must not keep the inside-wall explanation")


def test_anchor_score_reference_uses_background_label_without_showing_old_score():
    adv = advisory(put_refs=["F_SPACE", "structure.anchor.score"])
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    card = evidence_card("EVIDENCE-ANCHOR-SCORE", advisory_payload=adv)
    rendered = render_cards([card])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    assert_true("历史锚贴合刻度（仅背景）" in text,
                "LLM references to legacy anchor score should use a background label")
    assert_true("价格锚来源状态" not in text and "价格锚来源状态" not in html,
                "legacy anchor source-quality wording should not leak into the reader")
    assert_true("57.8" not in text,
                "legacy anchor fit score value should not be displayed in market facts")
    assert_true('href="#market-options-structure"' in html,
                "legacy anchor score references should still jump to the price-anchor fact group")

def test_v2_action_panel_tone_uses_local_state_not_highest_grade():
    adv = advisory(put_grade="B", call_grade="A", action_summary="Call 侧证据等级 A，但当前边界仍阻断人工准备。")
    for key in ("put_credit", "call_credit"):
        adv["local_action_state"][key] = {
            "state": "BLOCKED",
            "label_cn": "当前阻断",
            "reasons_cn": ["本地边界暂不允许进入人工准备。"],
        }
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    rendered = render_cards([evidence_card("EVIDENCE-CALL-A-BLOCKED", advisory_payload=adv)])
    html = rendered["documentHtml"]
    panel_class = first_comfort_panel_class(html)
    assert_true("is-grade-d" in panel_class,
                "top action panel should use blocked local action state for tone")
    assert_true("is-grade-a" not in panel_class and "is-grade-s" not in panel_class,
                "top action panel should not inherit the highest side evidence grade")
    assert_true('comfort-side-card is-grade-a' in html and 'badge is-grade-a' in html,
                "side evidence cards should keep their own grade coloring")
    assert_true('evidence-action-grid' not in html,
                "top action status grid should not duplicate side actions")


def test_v2_llm_review_keeps_same_view_and_orders_support_counter_action():
    rendered = render_cards([evidence_card()])
    html = rendered["documentHtml"]
    decision = html_section(html, "signal-comfort")
    review = html_section(html, "signal-llm-review")
    assert_true("LLM独立复核意见" in review.replace(" ", ""),
                "same-call LLM review should have its own report module")
    assert_true("下方空间约束仍有效" in decision and "下方空间约束仍有效" not in review,
                "valid side support thesis should live in the top decision module, not be duplicated in review")
    assert_true('href="#signal-comfort"' in review and "本侧评级判断" in review,
                "independent review should link valid support back to the adopted top rating")
    assert_true("价格仍在锚带内" in review and "主动成交分歧可能说明压力尚未完成传导" in review,
                "independent review should keep counter and alternative explanations from the same v2 view")
    assert_true("重新评级" not in review and "旧综合建议" not in review,
                "independent review should not create another rating layer")
    support_pos = review.find("支持解释")
    counter_pos = review.find("主要反证")
    action_pos = review.find("行动状态")
    assert_true(0 <= support_pos < counter_pos < action_pos,
                "side review should read support, counter evidence, then local action")
    assert_true("action_summary_cn" not in review and "side_evidence_ratings" not in review,
                "same-view review should not expose internal payload keys")


def test_v2_unrated_side_remains_marked_in_independent_review():
    adv = advisory(put_grade=None, call_grade="B", put_refs=[])
    adv["side_evidence_ratings"]["put_credit"]["status"] = "UNRATED"
    adv["side_evidence_ratings"]["put_credit"]["basis_cn"] = "Put 侧关键引用未通过核验，暂不采纳本侧评级。"
    adv["side_evidence_ratings"]["put_credit"]["validation_reasons_cn"] = ["无法核验的事实引用。"]
    adv["local_action_state"]["put_credit"] = {
        "state": "UNRATED",
        "label_cn": "暂未评级",
        "reasons_cn": ["本侧评审未采纳。"],
    }
    adv["action_summary_cn"] = "Call 侧证据等级 B；Put 侧暂未采纳。"
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    rendered = render_cards([evidence_card("EVIDENCE-UNRATED-SIDE", advisory_payload=adv)])
    html = rendered["documentHtml"]
    decision = html_section(html, "signal-comfort")
    review = html_section(html, "signal-llm-review")
    assert_true("上方空间也可读，但当前支持优势不足" in decision,
                "valid side basis should remain visible in the current top decision module")
    assert_true("Put 侧关键引用未通过核验" not in decision,
                "unrated side basis must not be presented as the current adopted decision")
    assert_true("未采纳的评审说明" in review and "Put 侧关键引用未通过核验" in review,
                "unrated side should keep an explicit not-adopted explanation")
    assert_true("暂未评级" in review and "无法核验的事实引用" in review,
                "unrated side should keep the local action label and validation gap")
    assert_no_machine_leak(rendered, context="v2 unrated side review")

def test_v2_filters_use_side_evidence_grades_and_ignore_legacy_comfort():
    high = evidence_card("EVIDENCE-A")
    low_adv = advisory(put_grade="C", call_grade="C", action_summary="两侧均为普通观察。")
    low = evidence_card("EVIDENCE-C", advisory_payload=low_adv)
    old = fixture_valid_as()
    rendered_all = render_cards([high, low, old])
    rendered_attention = render_cards([high, low, old], grade_filter="attention")
    rendered_admission = render_cards([high, low, old], grade_filter="admission")
    assert_true("旧版行动评级" in rendered_all["indexText"],
                "legacy comfort cards should remain readable in the unfiltered list")
    assert_true("EVIDENCE-A" in rendered_attention["indexHtml"] and "EVIDENCE-C" not in rendered_attention["indexHtml"],
                "B及以上 filter should keep only v2 side grades at B or above")
    assert_true("COMFORT-AS" not in rendered_attention["indexHtml"],
                "legacy comfort final_grade must not enter v2 evidence filters")
    assert_true("EVIDENCE-A" in rendered_admission["indexHtml"] and "EVIDENCE-C" not in rendered_admission["indexHtml"],
                "A/S filter should use v2 side evidence grades")
    empty = render_cards([low], grade_filter="admission")
    assert_true("当前筛选没有匹配的信号卡" in empty["documentText"],
                "empty filters must explain that no matching detail is selected")
    assert_true("两侧均为普通观察" not in empty["documentText"] and "comfort-side-head" not in empty["documentHtml"],
                "empty filters must not retain a previous card's grade or action")


def test_all_v2_collection_hides_legacy_filter_groups_and_clears_options():
    rendered = render_cards([evidence_card("EVIDENCE-A"), evidence_card("EVIDENCE-B", advisory_payload=advisory(put_grade="B", call_grade="C"))])
    filters = rendered["filterState"]
    assert_true(filters["directionHidden"] and filters["actionHidden"] and filters["qualityHidden"],
                "all-v2 collections should hide legacy direction/action/quality filters")
    assert_true(filters["directionValue"] == "" and filters["actionValue"] == "" and filters["qualityValue"] == "",
                "hidden legacy filters should reset stale selected values")
    for key in ("directionOptions", "actionOptions", "qualityOptions"):
        assert_true("BULLISH_STRONG" not in filters[key] and "TRADE_SUPPORT_REVIEW" not in filters[key] and "OK" not in filters[key],
                    "hidden legacy filters should not keep old field values in DOM: " + key)

    mixed = render_cards([evidence_card("EVIDENCE-A"), fixture_valid_as()])
    mixed_filters = mixed["filterState"]
    assert_true(not mixed_filters["directionHidden"] and not mixed_filters["actionHidden"] and not mixed_filters["qualityHidden"],
                "mixed collections should keep historical filters available for legacy cards")


def test_v2_market_facts_use_group_overview_with_details_for_full_fact_set():
    card = evidence_card_with_many_facts()
    rendered = render_cards([card])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    parser = MarketFactVisibilityParser()
    parser.feed(html)
    assert_true("中文市场事实" in text and "来源时点与解释边界" in text,
                "market facts should provide visible facts and secondary collection context")
    assert_true('class="market-fact-name"' in html and 'class="market-fact-value"' in html and 'class="market-fact-reading"' in html,
                "market facts should render as report rows with name, value and reading columns")
    assert_true("另有" not in text and "完整展开区" not in text,
                "large fact groups should not hide facts behind a slice counter")
    assert_true(parser.visible_items >= 38 and parser.details_items == 0,
                "all Chinese fact overview items should be visible outside details")
    assert_true("补充事实 34" in text and "第 34 条补充事实保留在主读区" in text,
                "late facts should be visible in the main reading flow")
    assert_true('href="#market-options-structure"' in html,
                "side evidence source links should still land on Chinese fact groups")
    assert_no_machine_leak(rendered, context="v2 many facts details")


def test_v2_market_fact_rows_keep_zero_and_missing_visible_by_default():
    rendered = render_cards([evidence_card_with_zero_and_missing_facts()])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    parser = MarketFactVisibilityParser()
    parser.feed(html)
    price_section = html_section(html, "market-price-path")
    quality_section = html_section(html, "market-data-quality")
    assert_true("零值压力读数" in price_section and "0 %" in price_section,
                "zero values are valid facts and must remain visible")
    assert_true("缺失但需阅读的事实" in quality_section and "未提供数值" in quality_section,
                "missing values should be shown as a visible gap, not hidden")
    assert_true("缺失本身是评级缺口" in text and "必要来源缺失" in text,
                "missing-fact explanations and limitations should remain in the main reader")
    for label in ("零值压力读数", "缺失但需阅读的事实"):
        matching = [item for item in parser.items if label in item["text"]]
        assert_true(len(matching) == 1 and not matching[0]["in_details"],
                    label + " should remain in its default-visible fact row")
    assert_no_machine_leak(rendered, context="v2 zero and missing facts")


def test_v2_fact_topic_routing_prefers_native_topic_over_source_refs():
    rendered = render_cards([evidence_card_with_topic_edges()])
    html = rendered["documentHtml"]
    text = rendered["documentText"]
    price_section = html_section(html, "market-price-path")
    active_section = html_section(html, "market-active-flow")
    quality_section = html_section(html, "market-data-quality")
    assert_true("价格响应来自主动流窗口" in price_section and "价格响应来自主动流窗口" not in active_section,
                "price response should stay in price-path even when its source references active flow")
    assert_true("量价主干压力" in price_section and "量价主干压力" not in active_section,
                "TMV pressure must not be presented as active trade confirmation")
    assert_true("四小时主动买卖流" in active_section and "四小时主动买卖流" not in price_section,
                "PRICE_FLOW source must keep CVD pressure distinct from price response")
    assert_true("期权偏斜方向" in html_section(html, "market-options-structure") and "期权偏斜方向" not in active_section,
                "option skew pressure must retain its options origin")
    assert_true("陈旧来源缺口" in quality_section and "本项暂不可用于评级" in quality_section,
                "source freshness and unusable facts should remain visible in data-quality")
    assert_true("OHLC 只能作为区间代理" in text and "陈旧事实不能冒充当前约束" in text,
                "path-proxy and stale-source limitations should remain in the main reader")
    assert_true("价格表现（2项）" in text,
                "same-destination evidence refs should be grouped into a reader-level source link")
    assert_no_machine_leak(rendered, context="v2 topic routing")


def test_v2_structure_facts_keep_values_sort_levels_and_remove_strict_mechanical_summary():
    rendered = render_cards([evidence_card_with_sorted_structure_facts()])
    html = rendered["documentHtml"]
    text = rendered["documentText"]
    structure = html_section(html, "market-options-structure")
    current_pos = structure.find("当前价格")
    call_wall_pos = structure.find("上方 Call 墙")
    call_distance_pos = structure.find("现价距上方 Call 墙")
    put_wall_pos = structure.find("下方 Put 墙")
    put_distance_pos = structure.find("现价距下方 Put 墙")
    assert_true(0 <= current_pos < call_wall_pos < call_distance_pos < put_wall_pos < put_distance_pos,
                "structure facts should sort current price, wall level and matching wall distance together")
    assert_true("100,000" in text and "103,000" in text and "2.81 %" in text,
                "non-empty fact values should remain visible as primary numbers")
    assert_true("当前价格为" not in text and "上方 Call 墙为" not in text and "现价距上方 Call 墙为" not in text,
                "strict label/value mechanical summaries should be removed")
    assert_true("墙位不是不可突破边界" in text,
                "shared market boundaries should remain visible")
    assert_true(text.find("现价距上方 Call 墙") < text.find("本组共同限制"),
                "group notes should not block the primary structure axis")
    assert_no_machine_leak(rendered, context="v2 sorted structure")


def test_v2_key_changes_show_verified_delta_without_reconstructing_previous_values():
    rendered = render_cards([evidence_card_with_verified_delta()])
    html = rendered["documentHtml"]
    changes = html_section(html, "signal-key-changes")
    assert_true("关键变化骨架" in changes and "现价变化" in changes,
                "key changes should render the verified-change module")
    assert_true("已核验差值" in changes and "-1.25 %" in changes,
                "verified delta should show the supplied delta value")
    assert_true("较前一卡下移" in changes and "只有差值，没有前值和后值" in changes,
                "verified delta should preserve meaning and limits")
    assert_true("前值" not in changes.replace("没有前值和后值", "") and "后值" not in changes.replace("没有前值和后值", ""),
                "key changes must not reconstruct previous/current values from a delta-only fact")
    assert_no_machine_leak(rendered, context="v2 verified delta changes")


def test_v2_key_changes_do_not_render_unverified_raw_transition_trap():
    rendered = render_cards([evidence_card_with_unusable_change_status()])
    html = rendered["documentHtml"]
    text = rendered["documentText"]
    changes = html_section(html, "signal-key-changes")
    quality = html_section(html, "market-data-quality")
    assert_true("前后对照暂不可用" in changes and 'href="#market-data-quality"' in changes,
                "unusable change context should keep the change module compact and link to the gap")
    assert_true("变化核验状态" in quality and "前后卡身份或时序未通过核验" in quality,
                "unusable change context should show the verified gap in data quality")
    assert_true("变化过程不可造" in quality and "当前截面事实仍可单独阅读" in quality,
                "unusable change context should keep limits visible in the detail boundary area")
    for token in ("RAW_TRANSITION_TRAP_SHOULD_NOT_RENDER", "伪造前值不应显示", "伪造后值不应显示"):
        assert_true(token not in text and token not in html,
                    "raw transition trap must not enter the reader: " + token)
    assert_no_machine_leak(rendered, context="v2 unusable change status")


def test_v2_hash_mismatch_invalidates_rating_but_keeps_market_facts():
    adv = advisory()
    card = evidence_card("EVIDENCE-HASH", advisory_payload=adv, summary_payload=evidence_summary(adv, override_hash="bad"))
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "hash mismatch should invalidate the v2 rating surface")
    assert_true("Put 侧证据等级 A" not in text, "invalid hash must not show the action-grade conclusion")
    assert_true("中文市场事实" in text and "空间约束" in text,
                "valid market facts should remain visible for manual review")
    assert_no_machine_leak(rendered, context="v2 hash mismatch")


def test_v2_side_reference_error_is_isolated_to_that_side():
    adv = advisory(put_grade="A", call_grade="B", put_refs=["F_MISSING"])
    adv["local_action_state"]["put_credit"] = {"state": "UNRATED", "label_cn": "暂未评级", "reasons_cn": []}
    adv["action_summary_cn"] = "Call 侧证据等级 B，先启动关注；Put 侧引用需复核。"
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    card = evidence_card("EVIDENCE-SIDE-ERROR", advisory_payload=adv)
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("Put 信用价差" in text and "未评级" in text,
                "bad Put references should isolate Put as unrated")
    assert_true("Call 信用价差" in text and "B级" in text,
                "valid Call side should remain readable")
    assert_true("无法核验的事实引用" in text, "reader should explain the isolated side problem")
    assert_no_machine_leak(rendered, context="v2 side isolation")


def test_python_generated_review_and_summary_feed_frontend_vm_with_prefixed_hash():
    card, review, summary = py_generated_review_card()
    packet = py_review_packet(card["identity"]["card_id"])
    advisory_payload = review["integrated_trade_advisory"]
    review_hash = advisory_payload["validation"]["assessment_hash"]
    assert_true(packet["facts"][0]["value"] == 1.0 and packet["facts"][1]["value"] == 1e-7,
                "Python review fixture should include numeric facts that stress browser JSON spelling")
    assert_true(review_hash.startswith("sha256:"), "Python review should persist prefixed assessment hash")
    assert_true(summary["assessment_hash"] == review_hash,
                "Python materializer summary should carry the same assessment hash")
    rendered = render_cards([card])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    assert_true("最高辅助交易决策" in text and "Put" in text and "A级" in text and "准入" in text,
                "frontend should accept the Python-generated v2 review")
    assert_true("暂未完成有效评级" not in text,
                "prefixed assessment hash should not invalidate the real v2 shape")
    assert_true("空间约束" in text and "主动成交" in text,
                "frontend should render Python-generated market facts in Chinese")
    assert_true("+0.0023%" in text and "0 decimal" not in text,
                "funding decimal values should render as signed percentages only in funding context")
    assert_true("3 天" in text and "3 个观察窗口" in text,
                "generic evidence windows should be translated for readers")
    assert_true("记录提供来源年龄" not in text and "不自行创造" not in text and "旧代码" not in text and "默认 0.4%" not in text,
                "implementation-oriented fact limitations should be rewritten as market boundaries")
    assert_true("已记录来源年龄" in text and "缺少显式带宽时不补造空间边界" in text,
                "rewritten fact limitations should keep the substantive boundary")
    for token in ("side_evidence_ratings", "local_action_state", "assessment_hash", "py_space"):
        assert_true(token not in text and token not in html,
                    "Python-generated review should not leak machine token: " + token)
    assert_no_machine_leak(rendered, context="python generated v2 review")


def test_legacy_reader_localizes_status_tokens_without_partial_confidence_fragments():
    card = fixture_valid_as()
    card["llm_review"]["content"]["data_quality_note"] = (
        "MISSING 来源与 MATERIAL 分歧已记录；CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE。"
    )
    card["conflict"]["explanation_cn"] = (
        "MATERIAL 反证仍在，若关键来源 MISSING，CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE。"
    )
    rendered = render_cards([card])
    combined = " ".join([
        rendered["documentText"],
        rendered["indexText"],
        rendered["documentHtml"],
        rendered["indexHtml"],
    ])
    assert_true("缺失" in combined and "实质分歧" in combined,
                "legacy reader should localize common status tokens")
    assert_true("置信门仅作限制，不提供方向依据" in combined,
                "legacy reader should localize the full confidence gate token before generic confidence replacement")
    for token in ("MISSING", "MATERIAL", "CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE", "旧置信_GATE_NOT_DIRECTIONAL_VOTE"):
        assert_true(token not in combined, "legacy reader should not leak status token: " + token)


def price_bias_fixture(bias="BEARISH"):
    return {
        "schema": "price_bias@1.0.0", "status": "ASSESSED", "bias": bias,
        "basis_cn": "下行压力得到价格响应，当前结构下价格倾向偏空。",
        "counter_cn": "若下方结构承接重新出现，下行压力可能减弱。",
        "invalid_if_cn": "主动流转向且价格收回结构参照时重新判断。",
        "evidence_refs": ["pressure.tmv.direction"],
        "counter_evidence_refs": ["structure.distance.put_wall_pct"],
        "validation_reasons_cn": [],
    }


def test_v2_price_bias_is_explicit_independent_and_locally_isolated():
    for bias, label in (("BULLISH", "偏多"), ("BEARISH", "偏空"), ("NEUTRAL", "中性"), ("MIXED", "多空分歧")):
        adv = advisory()
        adv["price_bias"] = price_bias_fixture(bias)
        adv["price_bias"]["basis_cn"] = "当前价格倾向为" + label + "，结合结构与压力继续观察。"
        adv["validation"]["assessment_hash"] = assessment_hash(adv)
        rendered = render_cards([evidence_card("direction-"+bias, advisory_payload=adv)])
        html = rendered["documentHtml"]
        assert_true("<strong>"+label+"</strong>" in html, "explicit price conclusion missing")
        assert_true(html.index("LLM 价格倾向") < html.index("适配论证"), "direction should lead independent review")
        assert_true("Put A级" in rendered["indexText"], "direction must not alter spread grade")
    historical = render_cards([evidence_card("direction-history")])["documentText"]
    assert_true("这张历史卡未单列 LLM 价格倾向" in historical, "historical direction must not be inferred")
    adv = advisory()
    adv["price_bias"] = price_bias_fixture("UNDETERMINED")
    adv["price_bias"].update(basis_cn="现有方向依据不足，暂不能形成偏多或偏空判断。", evidence_refs=[], counter_evidence_refs=[])
    adv["validation"]["assessment_hash"] = assessment_hash(adv)
    unknown = render_cards([evidence_card("direction-unknown", advisory_payload=adv)])["documentHtml"]
    assert_true("<strong>方向依据不足</strong>" in unknown, "valid uncertainty must not be presented as a format failure")
    for defect in ("invalid_status", "fabricated_ref", "future_ref", "bad_enum", "unsafe_text"):
        adv = advisory()
        adv["price_bias"] = price_bias_fixture()
        if defect == "invalid_status": adv["price_bias"]["status"] = "UNAVAILABLE"
        if defect == "fabricated_ref": adv["price_bias"]["evidence_refs"] = ["invented.fact"]
        if defect == "future_ref": update_market_fact(adv, "pressure.tmv.direction", observed_at_ms=AS_OF_MS+1)
        if defect == "bad_enum": adv["price_bias"]["bias"] = "UNKNOWN_MACHINE_ENUM"
        if defect == "unsafe_text": adv["price_bias"]["basis_cn"] = "factor_cross_section.price 显示偏空。"
        adv["validation"]["assessment_hash"] = assessment_hash(adv)
        rendered = render_cards([evidence_card("direction-"+defect, advisory_payload=adv)])
        assert_true("价格倾向尚未通过有效核验" in rendered["documentText"], "direction defect must be isolated: "+defect)
        assert_true("Put A级" in rendered["indexText"], "direction failure must preserve grades")
        assert_true("invented.fact" not in rendered["documentText"] and "UNKNOWN_MACHINE_ENUM" not in rendered["documentText"], "invalid direction leaked raw text")


def test_v2_net_gamma_keeps_compact_currency_unit():
    rendered = render_cards([evidence_card_with_spatial_overrides("gamma-m", gamma=243840000)])
    assert_true("+$243.84M" in rendered["documentText"], "net gamma should use compact USD M")
    assert_true("百万美元" not in rendered["documentText"], "no unnecessary unit translation")


def main():
    test_v2_price_bias_is_explicit_independent_and_locally_isolated()
    test_v2_net_gamma_keeps_compact_currency_unit()
    test_v2_reader_uses_single_evidence_surface_without_legacy_layers()
    test_v2_spatial_dynamics_keeps_chinese_transition_regime_undecided()
    test_v2_spatial_dynamics_reports_english_gamma_regime_conflict()
    test_v2_spatial_feedback_is_mirrored_without_changing_grades()
    test_v2_spatial_dynamics_does_not_infer_feedback_from_net_gamma_without_regime()
    test_v2_spatial_dynamics_distinguishes_inside_nearest_wall_and_outside_wall()
    test_anchor_score_reference_uses_background_label_without_showing_old_score()
    test_v2_action_panel_tone_uses_local_state_not_highest_grade()
    test_v2_llm_review_keeps_same_view_and_orders_support_counter_action()
    test_v2_unrated_side_remains_marked_in_independent_review()
    test_v2_filters_use_side_evidence_grades_and_ignore_legacy_comfort()
    test_all_v2_collection_hides_legacy_filter_groups_and_clears_options()
    test_v2_market_facts_use_group_overview_with_details_for_full_fact_set()
    test_v2_market_fact_rows_keep_zero_and_missing_visible_by_default()
    test_v2_fact_topic_routing_prefers_native_topic_over_source_refs()
    test_v2_structure_facts_keep_values_sort_levels_and_remove_strict_mechanical_summary()
    test_v2_key_changes_show_verified_delta_without_reconstructing_previous_values()
    test_v2_key_changes_do_not_render_unverified_raw_transition_trap()
    test_v2_hash_mismatch_invalidates_rating_but_keeps_market_facts()
    test_v2_side_reference_error_is_isolated_to_that_side()
    test_python_generated_review_and_summary_feed_frontend_vm_with_prefixed_hash()
    test_legacy_reader_localizes_status_tokens_without_partial_confidence_fragments()
    print("signal_evidence_frontend: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_evidence_frontend: FAIL - " + str(exc))
        sys.exit(1)
