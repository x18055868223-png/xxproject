"""Deterministic evidence packet builder for Astra v2 reviews.

This module is deliberately read-only.  It extracts a small, stable set of
market facts from a signal audit card and prepares them for the v2 LLM review.
It does not recompute legacy direction, scoring, durability, blocking, or
execution permission.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math


PACKET_SCHEMA_VERSION = "signal_evidence_packet@2.0.0"
FACT_KEYS = (
    "id",
    "topic",
    "label_cn",
    "value",
    "unit",
    "source_refs",
    "source_group",
    "observed_at_ms",
    "window",
    "usable",
    "summary_cn",
    "limitations_cn",
    "dependencies",
)

_BAD_STATUS_TOKENS = {
    "BAD",
    "BROKEN",
    "ERROR",
    "EXPIRED",
    "FAIL",
    "FAILED",
    "INVALID",
    "MISSING",
    "STALE",
    "UNAVAILABLE",
}

_TOPIC_ORDER = {
    "structure_location": 10,
    "adverse_pressure": 20,
    "price_response": 30,
    "change_context": 40,
    "source_quality": 50,
}

_SOURCE_TIME_KEYS = (
    "observed_at_ms",
    "timestamp_ms",
    "source_ts_ms",
    "gex_source_ts_ms",
    "event_time_ms",
    "complete_ts_ms",
    "updated_at_ms",
    "created_at_ms",
    "observed_at",
    "timestamp",
    "updated_at",
    "created_at",
)


def build_evidence_packet(card, previous_card=None, transition=None):
    """Build a v2 evidence packet from one signal audit card.

    ``previous_card`` and ``transition`` are used only when the transition
    proves that both cards match by identity, hash, time, symbol, strategy
    version, and schema.  Otherwise current-card facts remain usable and the
    change facts are marked unavailable.
    """
    current = _dict(card)
    previous = _dict(previous_card)
    identity = _identity(current)
    as_of_ms = _event_time_ms(current)
    packet = {
        "schema": PACKET_SCHEMA_VERSION,
        "identity": {
            "card_id": identity.get("card_id") or current.get("card_id"),
            "symbol": identity.get("symbol") or current.get("symbol"),
            "strategy_version": identity.get("strategy_version"),
            "as_of_ms": as_of_ms,
            "source_record_hash": _record_hash(current),
        },
        "facts": [],
        "limitations_cn": [
            "本包只服务总体证据评级，不给出胜率、收益概率、交易许可或具体两腿报价。",
            "本包只保留当前截面、同窗口关系与通过核验的前后变化；原始审计资料另行归档。",
        ],
    }

    facts = []
    _add_snapshot_fact(facts, current, as_of_ms)
    _add_market_price_fact(facts, current, as_of_ms)
    _add_structure_facts(facts, current, as_of_ms)
    _add_pressure_facts(facts, current, as_of_ms)
    _add_price_response_facts(facts, current, as_of_ms)
    _add_change_facts(facts, current, previous, _dict(transition), as_of_ms)
    _add_source_quality_fact(facts, current, as_of_ms)

    facts.sort(key=lambda item: (_TOPIC_ORDER.get(item["topic"], 99), item["id"]))
    packet["facts"] = facts
    return packet


def packet_hash(packet):
    """Return the canonical SHA-256 hash for a packet or packet fragment."""
    text = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _add_snapshot_fact(facts, card, as_of_ms):
    identity = _identity(card)
    event_type = str(identity.get("event_type") or "").upper()
    tags = [str(item).upper() for item in _list(identity.get("tags"))]
    analysis_round = _dict(card.get("analysis_round"))
    if (event_type == "FIXED_ANALYSIS_ROUND"
            or "FIXED_ROUND_ANALYSIS" in tags
            or analysis_round):
        value = "固定轮次"
        detail = str(analysis_round.get("label_cn") or "固定轮次分析").strip()
        summary = f"{detail}，只说明这是按时间截取的审计卡。"
    elif event_type:
        value = _cn_event_type(event_type)
        summary = f"本卡是{value}，用于区分事件卡与固定轮次。"
    else:
        value = "未知"
        summary = "卡片未提供可读事件类型；只影响事件/固定轮次分组。"
    _append_fact(
        facts,
        fact_id="snapshot.type",
        topic="source_quality",
        label_cn="卡片来源类型",
        value=value,
        unit=None,
        source_refs=["identity.event_type", "analysis_round"],
        source_group="CARD_CONTEXT",
        observed_at_ms=as_of_ms,
        window=None,
        usable=value != "未知",
        summary_cn=summary,
        limitations_cn=["来源类型不参与市场证据投票。"],
    )


def _add_market_price_fact(facts, card, as_of_ms):
    price = _market_price(card)
    quote = _market_quote(card) or "USDT"
    if price is None:
        _append_fact(
            facts,
            fact_id="market.price.current",
            topic="structure_location",
            label_cn="当前标的价格",
            value="缺失",
            unit=quote,
            source_refs=["market_context.price"],
            source_group="PRICE",
            observed_at_ms=as_of_ms,
            window="卡片时点",
            usable=False,
            summary_cn="当前标的价格缺失，空间距离和价格响应无法完整计算。",
            limitations_cn=["缺失价格只影响依赖价格的事实。"],
        )
        return
    _append_fact(
        facts,
        fact_id="market.price.current",
        topic="structure_location",
        label_cn="当前标的价格",
        value=_round_number(price),
        unit=quote,
        source_refs=["market_context.price"],
        source_group="PRICE",
        observed_at_ms=as_of_ms,
        window="卡片时点",
        usable=True,
        summary_cn=f"当前标的价格为 {_fmt_number(price)} {quote}，作为本卡空间距离的共同基准。",
        limitations_cn=["该价格不代表可成交期权报价。"],
    )


def _add_structure_facts(facts, card, as_of_ms):
    factor = _dict(card.get("factor_cross_section"))
    anchor = _dict(factor.get("anchor"))
    gamma = _dict(factor.get("gamma_regime"))
    gex = _dict(factor.get("gex_info"))
    price = _market_price(card)

    anchor_time = _source_time(anchor, as_of_ms)
    anchor_usable = _source_usable(card, "anchor", anchor)
    anchor_axis, axis_source = _anchor_axis(anchor)
    if anchor_axis is not None:
        _append_fact(
            facts,
            fact_id="structure.anchor.axis_price",
            topic="structure_location",
            label_cn="价格锚轴",
            value=_round_number(anchor_axis),
            unit=_market_quote(card) or "USDT",
            source_refs=["factor_cross_section.anchor"],
            source_group="OPTIONS_STRUCTURE",
            observed_at_ms=anchor_time,
            window=_window_from(anchor, "当前截面"),
            usable=anchor_usable,
            summary_cn=f"价格锚轴为 {_fmt_number(anchor_axis)}；来源为{axis_source}。",
            limitations_cn=_source_limitations(anchor, [
                "只采用已记录的锚轴；不使用现价回退补造真实锚。",
            ]),
        )

    band_half = _explicit_anchor_band_half(anchor)
    if band_half is not None:
        _append_fact(
            facts,
            fact_id="structure.anchor.band_half",
            topic="structure_location",
            label_cn="价格锚显式半带宽",
            value=_round_number(band_half),
            unit=_market_quote(card) or "USDT",
            source_refs=["factor_cross_section.anchor"],
            source_group="OPTIONS_STRUCTURE",
            observed_at_ms=anchor_time,
            window=_window_from(anchor, "当前截面"),
            usable=anchor_usable,
            summary_cn=f"价格锚显式半带宽为 {_fmt_number(band_half)}。",
            limitations_cn=["只记录显式带宽；不采用旧代码的默认 0.4% 带宽。"],
            dependencies=["structure.anchor.axis_price"],
        )
        if price is not None and anchor_axis is not None and band_half > 0:
            ratio = (price - anchor_axis) / band_half
            position = "带内" if abs(ratio) <= 1 else ("带上方" if ratio > 0 else "带下方")
            _append_fact(
                facts,
                fact_id="structure.anchor.band_position",
                topic="structure_location",
                label_cn="现价相对价格锚带",
                value=position,
                unit=None,
                source_refs=["market_context.price", "factor_cross_section.anchor"],
                source_group="DERIVED_RELATION",
                observed_at_ms=as_of_ms,
                window="卡片时点",
                usable=anchor_usable,
                summary_cn=f"现价相对显式锚带为{position}，距离约 {_fmt_number(ratio)} 个半带宽。",
                limitations_cn=["这是当前截面关系，不证明未来约束寿命。"],
                dependencies=["market.price.current", "structure.anchor.axis_price", "structure.anchor.band_half"],
            )
    elif anchor_axis is not None:
        _append_fact(
            facts,
            fact_id="structure.anchor.band_position",
            topic="structure_location",
            label_cn="现价相对价格锚带",
            value="未知",
            unit=None,
            source_refs=["factor_cross_section.anchor"],
            source_group="DERIVED_RELATION",
            observed_at_ms=as_of_ms,
            window="卡片时点",
            usable=False,
            summary_cn="价格锚没有显式带宽，本轮不判断带内或带外。",
            limitations_cn=["没有显式带宽时，只保留锚轴距离，不证明真实约束区间。"],
            dependencies=["structure.anchor.axis_price"],
        )

    if price is not None and anchor_axis is not None and anchor_axis:
        distance_pct = (price - anchor_axis) / abs(anchor_axis) * 100.0
        _append_fact(
            facts,
            fact_id="structure.anchor.axis_distance_pct",
            topic="structure_location",
            label_cn="现价相对价格锚轴距离",
            value=_round_number(distance_pct),
            unit="%",
            source_refs=["market_context.price", "factor_cross_section.anchor"],
            source_group="DERIVED_RELATION",
            observed_at_ms=as_of_ms,
            window="卡片时点",
            usable=anchor_usable,
            summary_cn=f"现价相对价格锚轴为 {_fmt_signed(distance_pct)}%。",
            limitations_cn=["距离只描述空间位置，不自动推出均值回归。"],
            dependencies=["market.price.current", "structure.anchor.axis_price"],
        )

    gamma_time = _source_time(gamma, as_of_ms) or _source_time(gex, as_of_ms)
    gamma_usable = _source_usable(card, "gamma_regime", gamma or gex)
    regime = gamma.get("regime") or gex.get("market_state")
    if regime not in (None, ""):
        _append_fact(
            facts,
            fact_id="structure.gamma.regime",
            topic="structure_location",
            label_cn="Gamma 结构状态",
            value=_cn_gamma_regime(regime),
            unit=None,
            source_refs=["factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
            source_group="OPTIONS_STRUCTURE",
            observed_at_ms=gamma_time,
            window=_window_from(gamma or gex, "当前截面"),
            usable=gamma_usable,
            summary_cn=f"Gamma/GEX 当前显示为{_cn_gamma_regime(regime)}。",
            limitations_cn=_gex_limitations(gex, [
                "Gamma/GEX 只描述空间和尾部约束，不单独决定方向。",
            ]),
        )
    else:
        _append_missing_fact(
            facts, "structure.gamma.regime", "structure_location",
            "Gamma 结构状态", "factor_cross_section.gamma_regime", "OPTIONS_STRUCTURE",
            "未找到 Gamma/GEX 状态，不能用正 Gamma 或负 Gamma 叙事补足。", gamma_time)

    net_gamma = _first_number(
        gex.get("net_gamma_notional_usd"),
        gex.get("total_net_gex"),
        gex.get("net_gamma_notional"),
        gamma.get("net_gamma_notional_usd"),
        gamma.get("net_gamma_notional"),
    )
    if net_gamma is not None and abs(net_gamma) >= 1.0:
        _append_fact(
            facts,
            fact_id="structure.gex.net_gamma_notional_usd",
            topic="structure_location",
            label_cn="净 Gamma 名义规模",
            value=_round_number(net_gamma),
            unit="USD",
            source_refs=["factor_cross_section.gex_info"],
            source_group="OPTIONS_STRUCTURE",
            observed_at_ms=gamma_time,
            window=_window_from(gex, "当前截面"),
            usable=_source_usable(card, "gex_info", gex),
            summary_cn=f"净 Gamma 名义规模约 {_fmt_number(net_gamma)} USD。",
            limitations_cn=_gex_limitations(gex, [
                "同属期权结构来源，不能与墙位、翻转点、Pin 重复当作多份独立确认。",
            ]),
        )

    _add_level_fact(facts, card, "structure.gamma.flip_point", "Gamma 翻转点",
                    _first_number(gamma.get("flip_point"), gex.get("flip_point")),
                    gamma_time, gamma_usable, ["factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
                    source_node=gamma or gex)
    _add_level_fact(facts, card, "structure.gamma.call_wall", "上方 Call 墙",
                    _first_number(gamma.get("call_wall"), gex.get("call_wall")),
                    gamma_time, gamma_usable, ["factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
                    source_node=gamma or gex)
    _add_level_fact(facts, card, "structure.gamma.put_wall", "下方 Put 墙",
                    _first_number(gamma.get("put_wall"), gex.get("put_wall")),
                    gamma_time, gamma_usable, ["factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
                    source_node=gamma or gex)
    _add_level_fact(facts, card, "structure.gamma.pin_strike", "Pin 或最大 Gamma 行权价",
                    _first_number(
                        gamma.get("pin_strike"),
                        _dict(gamma.get("pin")).get("pin_strike"),
                        gex.get("pin_strike"),
                        gex.get("max_gamma_strike"),
                        gamma.get("max_gamma_strike"),
                    ),
                    gamma_time, gamma_usable, ["factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
                    source_node=gamma or gex)

    _add_spatial_relation_facts(facts, card, price, gamma, gex, gamma_time,
                                gamma_usable)


def _add_pressure_facts(facts, card, as_of_ms):
    factor = _dict(card.get("factor_cross_section"))
    tmvf = _dict(factor.get("tmvf"))
    micro = _dict(factor.get("micro_flow"))
    macro = _dict(factor.get("macro_pressure"))
    funding = _dict(factor.get("funding"))
    skew = _dict(factor.get("skew"))

    tmv_time = _source_time(tmvf, as_of_ms)
    tmv_usable = _source_usable(card, "tmvf", tmvf)
    direction = tmvf.get("direction") or _dict(tmvf.get("combined")).get("direction")
    tmv_blend = _first_number(tmvf.get("tmv_blend"),
                              _dict(tmvf.get("combined")).get("tmv_blend"))
    if direction not in (None, ""):
        _append_fact(
            facts,
            fact_id="pressure.tmv.direction",
            topic="adverse_pressure",
            label_cn="量价主干方向",
            value=_cn_direction(direction),
            unit=None,
            source_refs=["factor_cross_section.tmvf"],
            source_group="PRICE_FLOW",
            observed_at_ms=tmv_time,
            window=_window_from(tmvf, "当前截面"),
            usable=tmv_usable,
            summary_cn=f"量价主干方向为{_cn_direction(direction)}。",
            limitations_cn=_source_limitations(tmvf, [
                "量价方向是压力背景，不等于垂直价差已经适配。",
            ]),
        )
    elif tmvf:
        _append_missing_fact(
            facts, "pressure.tmv.direction", "adverse_pressure",
            "量价主干方向", "factor_cross_section.tmvf", "PRICE_FLOW",
            "TMV 对象存在，但没有可读方向。", tmv_time)
    if tmv_blend is not None:
        _append_fact(
            facts,
            fact_id="pressure.tmv.blend",
            topic="adverse_pressure",
            label_cn="量价主干强度刻度",
            value=_round_number(tmv_blend),
            unit=None,
            source_refs=["factor_cross_section.tmvf"],
            source_group="PRICE_FLOW",
            observed_at_ms=tmv_time,
            window=_window_from(tmvf, "当前截面"),
            usable=tmv_usable,
            summary_cn=f"量价主干刻度为 {_fmt_number(tmv_blend)}。",
            limitations_cn=["该刻度不是胜率或收益概率，也不是本轮证据等级。"],
            dependencies=["pressure.tmv.direction"],
        )

    _add_cvd_window_facts(facts, card, micro, "fast_4h", "4小时", as_of_ms)
    _add_cvd_window_facts(facts, card, micro, "slow_12h", "12小时", as_of_ms)
    combined = _dict(micro.get("combined"))
    combined_direction = combined.get("direction")
    if combined_direction not in (None, ""):
        _append_fact(
            facts,
            fact_id="pressure.cvd.combined_direction",
            topic="adverse_pressure",
            label_cn="主动买卖流综合方向",
            value=_cn_direction(combined_direction),
            unit=None,
            source_refs=["factor_cross_section.micro_flow.combined"],
            source_group="PRICE_FLOW",
            observed_at_ms=_source_time(combined, as_of_ms) or _source_time(micro, as_of_ms),
            window=_window_from(combined, "当前截面"),
            usable=_source_usable(card, "micro_flow", combined or micro),
            summary_cn=f"主动买卖流综合方向为{_cn_direction(combined_direction)}。",
            limitations_cn=["该综合方向与 4h/12h 窗口同源，不能重复当作独立票。"],
        )

    macro_time = _source_time(macro, as_of_ms)
    macro_usable = _source_usable(card, "macro_pressure", macro)
    macro_score = _first_number(macro.get("macro_score"), macro.get("score"))
    if macro_score is not None:
        _append_fact(
            facts,
            fact_id="pressure.macro.score",
            topic="adverse_pressure",
            label_cn="宏观逆风刻度",
            value=_round_number(macro_score),
            unit=None,
            source_refs=["factor_cross_section.macro_pressure"],
            source_group="MACRO_CONTEXT",
            observed_at_ms=macro_time,
            window=_window_from(macro, "当前截面"),
            usable=macro_usable,
            summary_cn=(f"宏观逆风刻度为 {_fmt_number(macro_score)}；"
                        "正值为风险资产逆风，负值为顺风，不能把负值解释为价格下行压力。"),
            limitations_cn=(
                ["宏观背景只解释方向压力，硬阻断以宏观冲击门为准。"]
                + _time_basis_limitations(macro)
            ),
        )
    shock = _dict(macro.get("macro_shock"))
    if shock or macro:
        blocked = shock.get("block")
        if blocked is True:
            value, summary = "冲击阻断", "宏观冲击门处于阻断状态。"
        elif blocked is False:
            value, summary = "未阻断", "宏观冲击门未给出硬阻断。"
        else:
            value, summary = "未知", "宏观冲击门没有可核验状态。"
        _append_fact(
            facts,
            fact_id="pressure.macro.shock_gate",
            topic="adverse_pressure",
            label_cn="宏观冲击门",
            value=value,
            unit=None,
            source_refs=["factor_cross_section.macro_pressure.macro_shock"],
            source_group="MACRO_CONTEXT",
            observed_at_ms=_source_time(shock, as_of_ms) or macro_time,
            window=_window_from(shock or macro, "当前截面"),
            usable=macro_usable and value != "未知",
            summary_cn=summary,
            limitations_cn=(
                ["该事实不改变既有程序化阻断，只供证据评级解释。"]
                + _time_basis_limitations(shock or macro)
            ),
        )

    _add_funding_facts(facts, card, funding, as_of_ms)
    _add_skew_facts(facts, card, skew, as_of_ms)


def _add_price_response_facts(facts, card, as_of_ms):
    response = _primary_price_response(card)
    if response["return_pct"] is None:
        _append_fact(
            facts,
            fact_id="response.price.primary_return_pct",
            topic="price_response",
            label_cn="价格响应幅度",
            value="未知",
            unit="%",
            source_refs=["market_context.price", "factor_cross_section.micro_flow"],
            source_group="PRICE_FLOW",
            observed_at_ms=None,
            window=response["window"],
            usable=False,
            summary_cn="未找到可核验价格变化；不从量价方向反推出已经推进。",
            limitations_cn=["缺少价格变化只影响推进和传导关系。"],
        )
    else:
        ret = response["return_pct"]
        move = _price_move_bucket(ret)
        _append_fact(
            facts,
            fact_id="response.price.primary_return_pct",
            topic="price_response",
            label_cn="价格响应幅度",
            value=_round_number(ret),
            unit="%",
            source_refs=response["source_refs"],
            source_group="PRICE_FLOW",
            observed_at_ms=response["observed_at_ms"],
            window=response["window"],
            usable=response["usable"],
            summary_cn=f"{response['window'] or '当前窗口'}价格变化为 {_fmt_signed(ret)}%，判定为{move}。",
            limitations_cn=response["limitations_cn"],
            dependencies=response["dependencies"],
        )

    for side, label, adverse_name in (
            ("put", "Put 信用价差不利侧推进", "下行"),
            ("call", "Call 信用价差不利侧推进", "上行")):
        fact_id = f"side.{side}.adverse_progress"
        value, summary = _side_adverse_progress(side, response["return_pct"])
        _append_fact(
            facts,
            fact_id=fact_id,
            topic="price_response",
            label_cn=label,
            value=value,
            unit=None,
            source_refs=response["source_refs"] or ["market_context.price"],
            source_group="DERIVED_RELATION",
            observed_at_ms=response["observed_at_ms"],
            window=response["window"],
            usable=response["return_pct"] is not None and response["usable"],
            summary_cn=summary,
            limitations_cn=[
                f"这里只描述{adverse_name}是否推进，不代表另一侧自动适合成交。",
                "末日垂直价差更关心空间约束；动量只是侵入压力的一部分。",
            ],
            dependencies=["response.price.primary_return_pct"],
        )

    flow_relation = _price_flow_relation(card)
    _append_fact(
        facts,
        fact_id="response.flow_price.relation",
        topic="price_response",
        label_cn="主动流与价格响应关系",
        value=flow_relation["value"],
        unit=None,
        source_refs=flow_relation["source_refs"],
        source_group="DERIVED_RELATION",
        observed_at_ms=flow_relation["observed_at_ms"],
        window=flow_relation["window"],
        usable=flow_relation["usable"],
        summary_cn=flow_relation["summary_cn"],
        limitations_cn=flow_relation["limitations_cn"],
        dependencies=flow_relation["dependencies"],
    )

    path = _raw_path_fact(card, as_of_ms)
    if path:
        _append_fact(facts, **path)


def _add_change_facts(facts, card, previous_card, transition, as_of_ms):
    if not previous_card and not transition:
        _append_fact(
            facts,
            fact_id="change.context.status",
            topic="change_context",
            label_cn="前后变化核验",
            value="变化不可用",
            unit=None,
            source_refs=["transition_context"],
            source_group="CHANGE_CONTEXT",
            observed_at_ms=as_of_ms,
            window=None,
            usable=False,
            summary_cn="未提供可对应的前一张记录；当前截面事实仍可使用，变化判断留空。",
            limitations_cn=["缺少可核验变化记录，不能自行合成前后变化。"],
        )
        return
    valid, reasons = _transition_matches(card, previous_card, transition)
    if not valid:
        _append_fact(
            facts,
            fact_id="change.context.status",
            topic="change_context",
            label_cn="前后变化核验",
            value="变化不可用",
            unit=None,
            source_refs=["transition_context"],
            source_group="CHANGE_CONTEXT",
            observed_at_ms=as_of_ms,
            window=None,
            usable=False,
            summary_cn="前后变化未通过身份、时间、品种、版本或资料结构 核验；当前截面事实仍可使用。",
            limitations_cn=reasons or ["缺少可核验变化记录。"],
        )
        return

    elapsed_ms = _as_ms_delta(transition.get("elapsed_ms"))
    elapsed_min = None if elapsed_ms is None else elapsed_ms / 60000.0
    _append_fact(
        facts,
        fact_id="change.context.status",
        topic="change_context",
        label_cn="前后变化核验",
        value="变化可用",
        unit=None,
        source_refs=["transition_context"],
        source_group="CHANGE_CONTEXT",
        observed_at_ms=as_of_ms,
        window=None if elapsed_min is None else f"{_fmt_number(elapsed_min)}分钟",
        usable=True,
        summary_cn="前后卡身份、时间、品种、版本和资料结构 已匹配，可以使用变化摘要。",
        limitations_cn=["变化事实只解释当前与前一张卡的差异，不代表独立样本。"],
    )

    prev_price = _market_price(previous_card)
    curr_price = _market_price(card)
    if prev_price is not None and curr_price is not None and prev_price:
        delta_pct = (curr_price - prev_price) / abs(prev_price) * 100.0
        _append_fact(
            facts,
            fact_id="change.price.delta_pct",
            topic="change_context",
            label_cn="现价相对前卡变化",
            value=_round_number(delta_pct),
            unit="%",
            source_refs=["market_context.price", "transition_context"],
            source_group="CHANGE_CONTEXT",
            observed_at_ms=as_of_ms,
            window=None if elapsed_min is None else f"{_fmt_number(elapsed_min)}分钟",
            usable=True,
            summary_cn=f"现价相对前卡变化 {_fmt_signed(delta_pct)}%。",
            limitations_cn=["这不是独立行情路径，只是两张卡时点差。"],
            dependencies=["market.price.current", "change.context.status"],
        )

    for fact_id, label, extractor in (
            ("change.structure.flip_delta_pct", "Gamma 翻转点相对前卡迁移",
             lambda item: _first_number(
                 _dict(_dict(item.get("factor_cross_section")).get("gamma_regime")).get("flip_point"),
                 _dict(_dict(item.get("factor_cross_section")).get("gex_info")).get("flip_point"))),
            ("change.structure.anchor_delta_pct", "价格锚轴相对前卡迁移",
             lambda item: _anchor_axis(_dict(
                 _dict(item.get("factor_cross_section")).get("anchor")))[0]),
            ("change.structure.call_wall_delta_pct", "Call 墙相对前卡迁移",
             lambda item: _first_number(
                 _dict(_dict(item.get("factor_cross_section")).get("gamma_regime")).get("call_wall"),
                 _dict(_dict(item.get("factor_cross_section")).get("gex_info")).get("call_wall"))),
            ("change.structure.put_wall_delta_pct", "Put 墙相对前卡迁移",
             lambda item: _first_number(
                 _dict(_dict(item.get("factor_cross_section")).get("gamma_regime")).get("put_wall"),
                 _dict(_dict(item.get("factor_cross_section")).get("gex_info")).get("put_wall"))),
    ):
        prev_value = extractor(previous_card)
        curr_value = extractor(card)
        if prev_value is None or curr_value is None or not prev_value:
            continue
        delta_pct = (curr_value - prev_value) / abs(prev_value) * 100.0
        _append_fact(
            facts,
            fact_id=fact_id,
            topic="change_context",
            label_cn=label,
            value=_round_number(delta_pct),
            unit="%",
            source_refs=["factor_cross_section.gamma_regime", "factor_cross_section.gex_info", "transition_context"],
            source_group="CHANGE_CONTEXT",
            observed_at_ms=as_of_ms,
            window=None if elapsed_min is None else f"{_fmt_number(elapsed_min)}分钟",
            usable=True,
            summary_cn=f"{label}为 {_fmt_signed(delta_pct)}%。",
            limitations_cn=["结构位迁移与现价移动分开记录，不自动挑选有利边界。"],
            dependencies=["change.context.status"],
        )


def _add_source_quality_fact(facts, card, as_of_ms):
    quality = _dict(card.get("quality"))
    overall = quality.get("overall") or quality.get("state")
    if overall in (None, ""):
        return
    usable = not _has_bad_status(overall)
    _append_fact(
        facts,
        fact_id="quality.overall",
        topic="source_quality",
        label_cn="数据质量总览",
        value=_cn_quality(overall),
        unit=None,
        source_refs=["quality"],
        source_group="SOURCE_QUALITY",
        observed_at_ms=as_of_ms,
        window="卡片时点",
        usable=usable,
        summary_cn=f"卡片数据质量总览为{_cn_quality(overall)}。",
        limitations_cn=["总览只辅助解释；单项事实仍按各自可用性处理。"],
    )


def _add_level_fact(facts, card, fact_id, label, value, observed_at_ms,
                    usable, source_refs, source_node=None):
    if value is None:
        return
    quote = _market_quote(card) or "USDT"
    _append_fact(
        facts,
        fact_id=fact_id,
        topic="structure_location",
        label_cn=label,
        value=_round_number(value),
        unit=quote,
        source_refs=source_refs,
        source_group="OPTIONS_STRUCTURE",
        observed_at_ms=observed_at_ms,
        window="当前截面",
        usable=usable,
        summary_cn=f"{label}为 {_fmt_number(value)} {quote}。",
        limitations_cn=(
            ["该结构位只用于空间关系，不等于可成交行权价建议。"]
            + _time_basis_limitations(source_node)
        ),
    )


def _add_spatial_relation_facts(facts, card, price, gamma, gex, observed_at_ms,
                                usable):
    if price is None or price <= 0:
        return
    quote = _market_quote(card) or "USDT"
    levels = (
        ("structure.distance.call_wall_pct", "现价距上方 Call 墙",
         _first_number(gamma.get("call_wall"), gex.get("call_wall")),
         "structure.gamma.call_wall"),
        ("structure.distance.put_wall_pct", "现价距下方 Put 墙",
         _first_number(gamma.get("put_wall"), gex.get("put_wall")),
         "structure.gamma.put_wall"),
        ("structure.distance.flip_pct", "现价距 Gamma 翻转点",
         _first_number(gamma.get("flip_point"), gex.get("flip_point")),
         "structure.gamma.flip_point"),
        ("structure.distance.pin_pct", "现价距 Pin 或最大 Gamma 行权价",
         _first_number(
             gamma.get("pin_strike"),
             _dict(gamma.get("pin")).get("pin_strike"),
             gex.get("pin_strike"),
             gex.get("max_gamma_strike"),
             gamma.get("max_gamma_strike")),
         "structure.gamma.pin_strike"),
    )
    for fact_id, label, level, dep in levels:
        if level is None or level <= 0:
            continue
        signed = (level - price) / price * 100.0
        if fact_id.endswith("put_wall_pct"):
            signed = (price - level) / price * 100.0
        _append_fact(
            facts,
            fact_id=fact_id,
            topic="structure_location",
            label_cn=label,
            value=_round_number(signed),
            unit="%",
            source_refs=["market_context.price", "factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
            source_group="DERIVED_RELATION",
            observed_at_ms=observed_at_ms,
            window="卡片时点",
            usable=usable,
            summary_cn=f"{label}约 {_fmt_signed(signed)}%，价格基准为 {_fmt_number(price)} {quote}。",
            limitations_cn=["距离是当前截面，不证明边界不可突破。"],
            dependencies=["market.price.current", dep],
        )

    call_wall = _first_number(gamma.get("call_wall"), gex.get("call_wall"))
    put_wall = _first_number(gamma.get("put_wall"), gex.get("put_wall"))
    if call_wall is not None and put_wall is not None:
        if put_wall <= price <= call_wall:
            value = "墙内"
            summary = "现价位于 Put 墙和 Call 墙之间。"
        elif price < put_wall:
            value = "下破 Put 墙"
            summary = "现价低于 Put 墙，Put 信用价差需要特别复核下方侵入压力。"
        else:
            value = "上破 Call 墙"
            summary = "现价高于 Call 墙，Call 信用价差需要特别复核上方侵入压力。"
        _append_fact(
            facts,
            fact_id="structure.location.wall_zone",
            topic="structure_location",
            label_cn="现价相对期权墙区间",
            value=value,
            unit=None,
            source_refs=["market_context.price", "factor_cross_section.gamma_regime", "factor_cross_section.gex_info"],
            source_group="DERIVED_RELATION",
            observed_at_ms=observed_at_ms,
            window="卡片时点",
            usable=usable,
            summary_cn=summary,
            limitations_cn=["墙内不等于安全，墙外也不自动等于可交易反向。"],
            dependencies=[
                "market.price.current",
                "structure.gamma.call_wall",
                "structure.gamma.put_wall",
            ],
        )


def _add_cvd_window_facts(facts, card, micro, key, label, as_of_ms):
    window = _dict(micro.get(key))
    if not window:
        return
    fact_prefix = "pressure.cvd.fast_4h" if key == "fast_4h" else "pressure.cvd.slow_12h"
    observed = _source_time(window, as_of_ms) or _source_time(micro, as_of_ms)
    usable = _source_usable(card, f"micro_flow.{key}", window)
    cvd_norm = _first_number(window.get("cvd_norm"))
    cvd_sum = _first_number(window.get("cvd_sum"))
    price_return = _first_number(window.get("price_return_pct"),
                                 window.get("momentum_return_pct"),
                                 window.get("price_move_pct"))
    if cvd_norm is not None:
        _append_fact(
            facts,
            fact_id=f"{fact_prefix}.cvd_norm",
            topic="adverse_pressure",
            label_cn=f"{label}主动流标准化方向",
            value=_round_number(cvd_norm),
            unit=None,
            source_refs=[f"factor_cross_section.micro_flow.{key}"],
            source_group="PRICE_FLOW",
            observed_at_ms=observed,
            window=label,
            usable=usable,
            summary_cn=f"{label}主动流标准化值为 {_fmt_number(cvd_norm)}。",
            limitations_cn=(
                ["该值与同窗口价格变化同源配对，不是额外独立票。"]
                + _time_basis_limitations(window)
            ),
        )
    if cvd_sum is not None:
        _append_fact(
            facts,
            fact_id=f"{fact_prefix}.cvd_sum",
            topic="adverse_pressure",
            label_cn=f"{label}主动流净额",
            value=_round_number(cvd_sum),
            unit=str(window.get("cvd_unit") or "BTC"),
            source_refs=[f"factor_cross_section.micro_flow.{key}"],
            source_group="PRICE_FLOW",
            observed_at_ms=observed,
            window=label,
            usable=usable,
            summary_cn=f"{label}主动流净额为 {_fmt_number(cvd_sum)} {window.get('cvd_unit') or 'BTC'}。",
            limitations_cn=(
                ["净额单位不与价格涨跌直接相减。"]
                + _time_basis_limitations(window)
            ),
            dependencies=[f"{fact_prefix}.cvd_norm"],
        )
    if price_return is not None:
        _append_fact(
            facts,
            fact_id=f"{fact_prefix}.price_return_pct",
            topic="price_response",
            label_cn=f"{label}价格变化",
            value=_round_number(price_return),
            unit="%",
            source_refs=["market_context.price", f"factor_cross_section.micro_flow.{key}"],
            source_group="PRICE_FLOW",
            observed_at_ms=observed,
            window=label,
            usable=usable,
            summary_cn=f"{label}价格变化为 {_fmt_signed(price_return)}%。",
            limitations_cn=(
                ["这是同窗口汇总变化，不证明每一步路径先后。"]
                + _time_basis_limitations(window)
            ),
        )


def _add_funding_facts(facts, card, funding, as_of_ms):
    if not funding:
        return
    semantics = _dict(funding.get("canonical_funding_semantics"))
    observed = _source_time(funding, as_of_ms) or _source_time(semantics, as_of_ms)
    usable = _source_usable(card, "funding", funding)
    raw_rate = _first_number(
        semantics.get("raw_funding_rate"),
        funding.get("last_rate"),
        funding.get("last_funding_rate"),
    )
    if raw_rate is None:
        _append_fact(
            facts,
            fact_id="pressure.funding.raw_rate",
            topic="adverse_pressure",
            label_cn="资金费率",
            value="缺失",
            unit="decimal",
            source_refs=["factor_cross_section.funding"],
            source_group="FUNDING",
            observed_at_ms=observed,
            window=_window_from(funding, "当前截面"),
            usable=False,
            summary_cn="资金费率原始值缺失，不能由诊断派生项反推拥挤或方向。",
            limitations_cn=(
                ["资金费率缺失只影响资金费率相关解释。"]
                + _time_basis_limitations(funding)
            ),
        )
    else:
        text = str(semantics.get("canonical_text_cn") or "").strip()
        if not text:
            text = f"资金费率为 {_fmt_signed(raw_rate * 100.0)}%。"
        _append_fact(
            facts,
            fact_id="pressure.funding.raw_rate",
            topic="adverse_pressure",
            label_cn="资金费率",
            value=_round_number(raw_rate),
            unit="decimal",
            source_refs=["factor_cross_section.funding"],
            source_group="FUNDING",
            observed_at_ms=observed,
            window=_window_from(funding, "当前截面"),
            usable=usable,
            summary_cn=text,
            limitations_cn=(
                ["资金费率只按原始费率和已封装语义解释，不由诊断派生项覆盖。"]
                + _time_basis_limitations(funding)
            ),
        )
    if semantics:
        role = semantics.get("edb_participation")
        vote = semantics.get("edb_vote_allowed")
        if role not in (None, "") or vote is not None:
            value = "计票候选" if vote is True else "非计票"
            _append_fact(
                facts,
                fact_id="pressure.funding.vote_role",
                topic="adverse_pressure",
                label_cn="资金费率计票角色",
                value=value,
                unit=None,
                source_refs=["factor_cross_section.funding.canonical_funding_semantics"],
                source_group="FUNDING",
                observed_at_ms=observed,
                window=_window_from(funding, "当前截面"),
                usable=usable,
                summary_cn=f"资金费率在当前证据账本中为{value}观察。",
                limitations_cn=(
                    ["非计票不等于无信息，但不能当作独立方向投票。"]
                    + _time_basis_limitations(semantics or funding)
                ),
                dependencies=["pressure.funding.raw_rate"],
            )


def _add_skew_facts(facts, card, skew, as_of_ms):
    if not skew:
        return
    observed = _source_time(skew, as_of_ms)
    usable = _source_usable(card, "skew", skew)
    vote = _first_number(skew.get("vote"), skew.get("rr_blend"))
    if vote is not None:
        _append_fact(
            facts,
            fact_id="pressure.skew.vote",
            topic="adverse_pressure",
            label_cn="期权偏斜方向",
            value=_round_number(vote),
            unit=None,
            source_refs=["factor_cross_section.skew"],
            source_group="OPTIONS_STRUCTURE",
            observed_at_ms=observed,
            window=_window_from(skew, "当前截面"),
            usable=usable,
            summary_cn=f"期权偏斜方向刻度为 {_fmt_number(vote)}。",
            limitations_cn=(
                ["偏斜与 GEX 同属期权来源，需避免重复确认。"]
                + _time_basis_limitations(skew)
            ),
        )


def _raw_path_fact(card, as_of_ms):
    points = _extract_price_points(card)
    if points:
        first = points[0]
        last = points[-1]
        move = None if not first else (last - first) / abs(first) * 100.0
        value = "精确价格点"
        summary = f"发现 {len(points)} 个价格点"
        if move is not None:
            summary += f"，首尾变化 {_fmt_signed(move)}%"
        summary += "。"
        return {
            "fact_id": "response.price.path_source",
            "topic": "price_response",
            "label_cn": "价格路径来源",
            "value": value,
            "unit": None,
            "source_refs": ["signal_durability.price_points", "price_anchor_durability.price_points"],
            "source_group": "PRICE_FLOW",
            "observed_at_ms": as_of_ms,
            "window": "卡片内价格点",
            "usable": True,
            "summary_cn": summary,
            "limitations_cn": ["价格点只来自卡片已记录内容，不补造缺失轨迹。"],
            "dependencies": ["response.price.primary_return_pct"],
        }
    ohlc = _extract_ohlc(card)
    if ohlc:
        open_v = ohlc.get("open")
        close_v = ohlc.get("close")
        move = None if not open_v else (close_v - open_v) / abs(open_v) * 100.0
        summary = "发现 OHLC 区间"
        if move is not None:
            summary += f"，开收变化 {_fmt_signed(move)}%"
        summary += "；OHLC 只能作路径代理，不能证明区间内先后顺序。"
        return {
            "fact_id": "response.price.path_source",
            "topic": "price_response",
            "label_cn": "价格路径来源",
            "value": "OHLC代理",
            "unit": None,
            "source_refs": ["price_anchor_durability.ohlc", "signal_durability.ohlc"],
            "source_group": "PRICE_FLOW",
            "observed_at_ms": as_of_ms,
            "window": "卡片内OHLC",
            "usable": True,
            "summary_cn": summary,
            "limitations_cn": ["OHLC 不能证明路径先后或吸收发生顺序。"],
            "dependencies": ["response.price.primary_return_pct"],
        }
    return None


def _primary_price_response(card):
    factor = _dict(card.get("factor_cross_section"))
    micro = _dict(factor.get("micro_flow"))
    for key, label in (("fast_4h", "4小时"), ("slow_12h", "12小时")):
        window = _dict(micro.get(key))
        value = _first_number(window.get("price_return_pct"),
                              window.get("momentum_return_pct"),
                              window.get("price_move_pct"))
        if value is None:
            continue
        return {
            "return_pct": value,
            "window": label,
            "source_refs": ["market_context.price", "factor_cross_section.micro_flow"],
            "observed_at_ms": _source_time(window, _event_time_ms(card)),
            "usable": _source_usable(card, f"micro_flow.{key}", window),
            "limitations_cn": ["同窗口汇总变化不能证明每一步路径先后。"],
            "dependencies": [f"pressure.cvd.{key}.price_return_pct"],
        }
    points = _extract_price_points(card)
    if len(points) >= 2 and points[0]:
        value = (points[-1] - points[0]) / abs(points[0]) * 100.0
        return {
            "return_pct": value,
            "window": "卡片内价格点",
            "source_refs": ["signal_durability.price_points", "price_anchor_durability.price_points"],
            "observed_at_ms": _event_time_ms(card),
            "usable": True,
            "limitations_cn": ["价格点只来自卡片已记录内容。"],
            "dependencies": [],
        }
    ohlc = _extract_ohlc(card)
    if ohlc and ohlc.get("open") not in (None, 0) and ohlc.get("close") is not None:
        value = (ohlc["close"] - ohlc["open"]) / abs(ohlc["open"]) * 100.0
        return {
            "return_pct": value,
            "window": "卡片内OHLC",
            "source_refs": ["price_anchor_durability.ohlc", "signal_durability.ohlc"],
            "observed_at_ms": _event_time_ms(card),
            "usable": True,
            "limitations_cn": ["OHLC 只能作代理，不能证明区间内路径先后。"],
            "dependencies": [],
        }
    return {
        "return_pct": None,
        "window": None,
        "source_refs": [],
        "observed_at_ms": None,
        "usable": False,
        "limitations_cn": [],
        "dependencies": [],
    }


def _price_flow_relation(card):
    factor = _dict(card.get("factor_cross_section"))
    micro = _dict(factor.get("micro_flow"))
    for key, label in (("fast_4h", "4小时"), ("slow_12h", "12小时")):
        window = _dict(micro.get(key))
        cvd = _first_number(window.get("cvd_norm"))
        price = _first_number(window.get("price_return_pct"),
                              window.get("momentum_return_pct"),
                              window.get("price_move_pct"))
        if cvd is None or price is None:
            continue
        usable = _source_usable(card, f"micro_flow.{key}", window)
        cvd_sign = _sign(cvd, 1e-9)
        price_sign = _sign(price, 0.03)
        if cvd_sign == 0 and price_sign == 0:
            value = "流价均平"
            summary = f"{label}主动流与价格变化都接近平盘。"
        elif price_sign == 0:
            value = "传导弱"
            summary = f"{label}主动流存在方向，但价格接近平盘，传导偏弱。"
        elif cvd_sign == 0:
            value = "价格单独移动"
            summary = f"{label}价格有变化，但主动流方向不足。"
        elif cvd_sign == price_sign:
            value = "同向推进"
            side = "上行" if price_sign > 0 else "下行"
            summary = f"{label}主动流与价格同向，显示{side}推进。"
        else:
            value = "流价分歧"
            summary = f"{label}主动流与价格方向相反，竞争解释需要保留。"
        return {
            "value": value,
            "source_refs": ["factor_cross_section.micro_flow", "market_context.price"],
            "observed_at_ms": _source_time(window, _event_time_ms(card)),
            "window": label,
            "usable": usable,
            "summary_cn": summary,
            "limitations_cn": [
                "该关系只描述同窗口响应，不声称看见隐藏订单或真实吸收队列。",
                "不同单位不直接相减。",
            ],
            "dependencies": [
                f"pressure.cvd.{key}.cvd_norm",
                f"pressure.cvd.{key}.price_return_pct",
            ],
        }
    return {
        "value": "未知",
        "source_refs": ["factor_cross_section.micro_flow", "market_context.price"],
        "observed_at_ms": None,
        "window": None,
        "usable": False,
        "summary_cn": "缺少同窗口主动流和价格变化，无法判断传导关系。",
        "limitations_cn": ["该缺口只影响流价关系。"],
        "dependencies": [],
    }


def _side_adverse_progress(side, return_pct):
    if return_pct is None:
        return "未知", "没有可核验价格变化，不能判断该侧不利推进。"
    sign = _sign(return_pct, 0.03)
    if sign == 0:
        return "平盘", "价格变化接近平盘，不判定为强趋势或明显侵入。"
    if side == "put":
        if sign < 0:
            return "下行推进", "价格向 Put 信用价差的不利方向推进。"
        return "未见下行推进", "价格没有向 Put 信用价差的不利方向推进。"
    if sign > 0:
        return "上行推进", "价格向 Call 信用价差的不利方向推进。"
    return "未见上行推进", "价格没有向 Call 信用价差的不利方向推进。"


def _price_move_bucket(return_pct):
    if return_pct is None:
        return "未知"
    if abs(return_pct) <= 0.03:
        return "平盘"
    if return_pct > 0:
        return "上行推进"
    return "下行推进"


def _transition_matches(card, previous_card, transition):
    reasons = []
    if not previous_card:
        return False, ["缺少前一张卡，不能使用变化记录。"]
    if not transition:
        return False, ["缺少可对应的前一张记录，不能自行合成变化证明。"]
    if transition.get("schema_version") != "signal_transition_record@1.0.0":
        reasons.append("变化记录协议版本不匹配或缺失。")

    curr_identity = _identity(card)
    prev_identity = _identity(previous_card)
    if curr_identity.get("strategy_version") != prev_identity.get("strategy_version"):
        reasons.append("前后卡策略版本不同，不用于变化推断。")
    if _schema_fingerprint(card) != _schema_fingerprint(previous_card):
        reasons.append("前后卡资料结构不同，不用于变化推断。")
    curr_id = curr_identity.get("card_id") or card.get("card_id")
    prev_id = prev_identity.get("card_id") or previous_card.get("card_id")
    if transition.get("current_card_id") != curr_id:
        reasons.append("当前记录身份不匹配。")
    if transition.get("previous_card_id") != prev_id:
        reasons.append("前一张记录身份不匹配。")

    curr_symbol = curr_identity.get("symbol") or card.get("symbol")
    prev_symbol = prev_identity.get("symbol") or previous_card.get("symbol")
    if transition.get("symbol") != curr_symbol or curr_symbol != prev_symbol:
        reasons.append("品种不匹配。")

    curr_ts = _event_time_ms(card)
    prev_ts = _event_time_ms(previous_card)
    if _as_ms(transition.get("current_ts_ms")) != curr_ts:
        reasons.append("当前记录时间不匹配。")
    if _as_ms(transition.get("previous_ts_ms")) != prev_ts:
        reasons.append("前一张记录时间不匹配。")
    if curr_ts is not None and prev_ts is not None and curr_ts <= prev_ts:
        reasons.append("前后卡时间顺序异常。")

    hashes = _dict(transition.get("producer_record_hashes"))
    curr_hash = _record_hash(card)
    prev_hash = _record_hash(previous_card)
    if not curr_hash or hashes.get("current") != curr_hash:
        reasons.append("当前记录来源校验不一致。")
    if not prev_hash or hashes.get("previous") != prev_hash:
        reasons.append("前一张记录来源校验不一致。")

    version_pairs = (
        ("当前策略版本", curr_identity.get("strategy_version"),
         ("current_strategy_version", "card_versions.current_strategy_version",
          "strategy_versions.current")),
        ("前卡策略版本", prev_identity.get("strategy_version"),
         ("previous_strategy_version", "card_versions.previous_strategy_version",
          "strategy_versions.previous")),
        ("当前卡结构协议", _schema_fingerprint(card),
         ("current_card_schema", "card_schemas.current", "source_schemas.current")),
        ("前卡结构协议", _schema_fingerprint(previous_card),
         ("previous_card_schema", "card_schemas.previous", "source_schemas.previous")),
    )
    for label, expected, paths in version_pairs:
        actual = _first_path(transition, paths)
        if actual in (None, ""):
            reasons.append(f"变化记录缺少{label}证明。")
        elif expected in (None, "") or actual != expected:
            reasons.append(f"{label}与卡片不匹配。")

    return not reasons, reasons


def _append_missing_fact(facts, fact_id, topic, label_cn, source_ref,
                         source_group, summary_cn, observed_at_ms):
    _append_fact(
        facts,
        fact_id=fact_id,
        topic=topic,
        label_cn=label_cn,
        value="缺失",
        unit=None,
        source_refs=[source_ref],
        source_group=source_group,
        observed_at_ms=observed_at_ms,
        window="当前截面",
        usable=False,
        summary_cn=summary_cn,
        limitations_cn=["缺失只影响依赖该来源的判断。"],
    )


def _append_fact(facts, *, fact_id, topic, label_cn, value, unit, source_refs,
                 source_group, observed_at_ms, window, usable, summary_cn,
                 limitations_cn=None, dependencies=None):
    fact = {
        "id": str(fact_id),
        "topic": str(topic),
        "label_cn": str(label_cn),
        "value": _scalar_value(value),
        "unit": None if unit in (None, "") else str(unit),
        "source_refs": _unique_strings(source_refs),
        "source_group": str(source_group),
        "observed_at_ms": _as_ms(observed_at_ms),
        "window": None if window in (None, "") else str(window),
        "usable": bool(usable),
        "summary_cn": str(summary_cn),
        "limitations_cn": _unique_strings(limitations_cn or []),
        "dependencies": _unique_strings(dependencies or []),
    }
    if set(fact) != set(FACT_KEYS):
        raise AssertionError("internal fact schema mismatch")
    facts.append(fact)


def _scalar_value(value):
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _finite_number(value)
        if number is None:
            return None
        return _round_number(number)
    if value is None:
        return None
    return str(value)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(*values):
    for value in values:
        number = _finite_number(value)
        if number is not None:
            return number
    return None


def _round_number(value):
    number = _finite_number(value)
    if number is None:
        return None
    return round(number, 8)


def _sign(value, eps):
    number = _finite_number(value)
    if number is None or abs(number) <= eps:
        return 0
    return 1 if number > 0 else -1


def _fmt_number(value):
    number = _finite_number(value)
    if number is None:
        return "-"
    text = f"{number:,.4f}"
    return text.rstrip("0").rstrip(".")


def _fmt_signed(value):
    number = _finite_number(value)
    if number is None:
        return "-"
    return f"{number:+.4f}".rstrip("0").rstrip(".")


def _unique_strings(values):
    out = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in out:
            out.append(text)
    return out


def _identity(card):
    return _dict(_dict(card).get("identity"))


def _event_time_ms(card):
    identity = _identity(card)
    for value in (
            _dict(_dict(card).get("provenance")).get("transition_audit_source", {}).get("event_time_ms")
            if isinstance(_dict(_dict(card).get("provenance")).get("transition_audit_source"), dict)
            else None,
            identity.get("confirmed_time_ms"),
            card.get("confirmed_time_ms"),
            identity.get("confirmed_at"),
            card.get("created_at")):
        parsed = _as_ms(value)
        if parsed is not None:
            return parsed
    return None


def _as_ms(value):
    if value in (None, ""):
        return None
    number = _finite_number(value)
    if number is not None:
        if number <= 0:
            return None
        if number < 100000000000 and number > 1000000000:
            number *= 1000.0
        return int(round(number))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _as_ms_delta(value):
    number = _finite_number(value)
    if number is None:
        return None
    return int(round(number))


def _record_hash(card):
    for path in (
            "producer_integrity.record_hash",
            "integrity.record_hash",
            "producer_record_hash",
            "source_record_hash",
            "record_hash"):
        value = _get_path(card, path)
        if value not in (None, ""):
            return str(value)
    return None


def _schema_fingerprint(card):
    schema = _dict(_dict(card).get("schema"))
    if schema:
        return packet_hash(schema)
    version = _dict(card).get("schema_version")
    if version not in (None, ""):
        return str(version)
    return None


def _market_price(card):
    market = _dict(_dict(card).get("market_context"))
    return _first_number(
        market.get("price"),
        market.get("market_price"),
        market.get("spot_price"),
        market.get("index_price"),
        card.get("price"),
        card.get("market_price"),
    )


def _market_quote(card):
    market = _dict(_dict(card).get("market_context"))
    return (
        market.get("quote_currency")
        or market.get("quote")
        or market.get("currency")
        or market.get("settlement_currency")
    )


def _anchor_axis(anchor):
    for key, label in (
            ("effective_flip_point", "有效结构轴"),
            ("anchor_price", "锚价"),
            ("flip_point", "翻转点"),
            ("raw_flip_point", "原始翻转点")):
        value = _first_number(anchor.get(key))
        if value is not None:
            return value, label
    return None, None


def _explicit_anchor_band_half(anchor):
    value = _first_number(
        anchor.get("band_half"),
        anchor.get("band_half_width"),
        _dict(anchor.get("band")).get("half_width"),
        _dict(anchor.get("facts")).get("band_half"),
    )
    return value if value is not None and value > 0 else None


def _source_time(node, card_as_of_ms):
    source = _dict(node)
    if not source:
        return None
    for key in _SOURCE_TIME_KEYS:
        parsed = _as_ms(source.get(key))
        if parsed is not None:
            return parsed
    age_ms = _finite_number(source.get("age_ms"))
    if age_ms is not None and card_as_of_ms is not None and age_ms >= 0:
        return int(card_as_of_ms - age_ms)
    return _as_ms(card_as_of_ms)


def _window_from(node, fallback=None):
    source = _dict(node)
    for key in ("window", "time_window", "clock_window"):
        value = source.get(key)
        if value not in (None, "", {}, []):
            if isinstance(value, dict):
                label = value.get("label_cn") or value.get("name") or value.get("clock_window")
                if label:
                    return str(label)
                continue
            return str(value)
    hours = _first_number(source.get("horizon_hours"),
                          source.get("interval_hours"),
                          source.get("target_expiry_hours"))
    if hours is not None:
        return f"{_fmt_number(hours)}小时"
    return fallback


def _source_usable(card, source_key, node):
    source = _dict(node)
    if not source:
        return False
    for key in ("data_ready", "ready", "available", "raw_available"):
        if source.get(key) is False:
            return False
    for key in ("freshness", "quality", "data_quality", "data_status",
                "status", "state"):
        if _has_bad_status(source.get(key)):
            return False
    status = _quality_source_status(card, source_key)
    if _has_bad_status(status):
        return False
    return True


def _quality_source_status(card, source_key):
    quality = _dict(_dict(card).get("quality"))
    sources = _dict(quality.get("sources"))
    keys = {str(source_key).lower()}
    for item in str(source_key).lower().replace(".", "_").split("_"):
        if item:
            keys.add(item)
    aliases = {
        "anchor": {"anchor", "gex", "gamma"},
        "gamma_regime": {"gamma", "gex", "ggr"},
        "gex_info": {"gamma", "gex", "ggr"},
        "tmvf": {"tmvf", "tmv", "price"},
        "micro_flow": {"micro_flow", "cvd", "flow"},
        "macro_pressure": {"macro", "macro_pressure"},
        "funding": {"funding"},
        "skew": {"skew", "srd"},
    }
    keys.update(aliases.get(str(source_key).split(".")[0], set()))
    for key, value in sources.items():
        if str(key).lower() not in keys:
            continue
        if isinstance(value, dict):
            return value.get("status") or value.get("quality") or value.get("state")
        return value
    return None


def _has_bad_status(value):
    if value in (None, ""):
        return False
    text = str(value).upper()
    return any(token in text for token in _BAD_STATUS_TOKENS)


def _source_limitations(node, base):
    limitations = list(base)
    source = _dict(node)
    freshness = source.get("freshness")
    if _has_bad_status(freshness):
        limitations.append("来源时效状态不可用或陈旧，依赖该来源的主张应降为缺口。")
    if source.get("data_ready") is False or source.get("ready") is False:
        limitations.append("来源显式未就绪。")
    if source.get("age_ms") not in (None, ""):
        limitations.append("记录提供来源年龄，但 v2 不自行创造新的新鲜度阈值。")
    limitations.extend(_time_basis_limitations(source))
    return _unique_strings(limitations)


def _time_basis_limitations(node):
    source = _dict(node)
    if source and not _has_source_timestamp(source) and source.get("age_ms") in (None, ""):
        return ["源端采集时间未单列；本事实时间为卡片记录时点。"]
    return []


def _has_source_timestamp(node):
    source = _dict(node)
    return any(source.get(key) not in (None, "") for key in _SOURCE_TIME_KEYS)


def _gex_limitations(gex, base):
    limitations = list(base)
    rank = _dict(gex.get("rank"))
    window = _dict(rank.get("window"))
    window_days = _first_number(
        window.get("window_days"),
        gex.get("window_days"),
    )
    rank_quality = gex.get("rank_quality") or rank.get("quality")
    if window_days is not None and window_days < 15.0:
        limitations.append("GEX 分位样本不足只影响分位解释，不否定当前墙位或名义规模事实。")
    if str(rank_quality or "").lower() == "warming_up":
        limitations.append("GEX 分位正在热身，只限制分位解释。")
    limitations.extend(_time_basis_limitations(gex))
    return _unique_strings(limitations)


def _extract_price_points(card):
    for node in _path_candidate_nodes(card):
        points = _dict(node).get("price_points")
        if isinstance(points, list):
            clean = [_finite_number(item) for item in points]
            clean = [item for item in clean if item is not None]
            if len(clean) >= 2:
                return clean
    return []


def _extract_ohlc(card):
    for node in _path_candidate_nodes(card):
        source = _dict(node)
        ohlc = _dict(source.get("ohlc")) or _dict(_dict(source.get("price_path")).get("ohlc"))
        if not ohlc:
            ohlc = source
        open_v = _first_number(ohlc.get("open"), ohlc.get("bar_open"),
                               ohlc.get("current_bar_open"))
        high_v = _first_number(ohlc.get("high"), ohlc.get("bar_high"),
                               ohlc.get("current_bar_high"))
        low_v = _first_number(ohlc.get("low"), ohlc.get("bar_low"),
                              ohlc.get("current_bar_low"))
        close_v = _first_number(ohlc.get("close"), ohlc.get("bar_close"),
                                ohlc.get("current_bar_close"),
                                ohlc.get("current_price"))
        if None not in (open_v, high_v, low_v, close_v):
            return {
                "open": open_v,
                "high": high_v,
                "low": low_v,
                "close": close_v,
            }
    return {}


def _path_candidate_nodes(card):
    root = _dict(card)
    durability = _dict(root.get("signal_durability"))
    anchor = _dict(root.get("price_anchor_durability"))
    nodes = [
        root,
        _dict(root.get("runtime_facts")),
        _dict(root.get("facts")),
        durability,
        anchor,
        _dict(_dict(durability.get("sublayers")).get("price_efficiency")),
        _dict(_dict(anchor.get("sublayers")).get("price_efficiency")),
        _dict(_dict(durability.get("layer_scores")).get("price_efficiency")),
        _dict(_dict(anchor.get("layer_scores")).get("price_efficiency")),
    ]
    return [node for node in nodes if node]


def _get_path(source, path):
    current = source
    for part in str(path).split("."):
        current = _dict(current).get(part)
        if current is None:
            return None
    return current


def _first_path(source, paths):
    for path in paths:
        value = _get_path(source, path)
        if value not in (None, ""):
            return value
    return None


def _cn_event_type(value):
    text = str(value or "").upper()
    if text == "NR_REPAIR_CONFIRMED":
        return "中性修复确认事件卡"
    if text == "FIXED_ANALYSIS_ROUND":
        return "固定轮次分析卡"
    return "事件卡"


def _cn_gamma_regime(value):
    text = str(value or "").replace("_", " ").upper()
    if "POSITIVE" in text or "LONG GAMMA" in text:
        return "正 Gamma 结构"
    if "NEGATIVE" in text or "SHORT GAMMA" in text:
        return "负 Gamma 结构"
    if "TRANSITION" in text:
        return "Gamma 过渡区"
    if "UNKNOWN" in text or not text.strip():
        return "未知"
    return str(value)


def _cn_direction(value):
    text = str(value or "").replace("_", " ").upper()
    if any(token in text for token in ("BULL", "BUY", "UP", "LONG")):
        return "上行"
    if any(token in text for token in ("BEAR", "SELL", "DOWN", "SHORT")):
        return "下行"
    if "NEUTRAL" in text or "FLAT" in text:
        return "中性"
    if "UNCLEAR" in text or "UNKNOWN" in text:
        return "不明"
    if "MIXED" in text or "CONFLICT" in text:
        return "分歧"
    return str(value)


def _cn_quality(value):
    text = str(value or "").upper()
    if text in {"OK", "GOOD", "VALID"}:
        return "可用"
    if "STALE" in text:
        return "陈旧"
    if "MISSING" in text:
        return "缺失"
    if "INVALID" in text or "ERROR" in text:
        return "不可用"
    return str(value)


def _deepcopy(value):
    return deepcopy(value)
