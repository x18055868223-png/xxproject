"""Decision Path: the reading order used to verify one frozen policy (2026-10-14).

The order of the steps is the experiment. It is defined here only (`STEPS`,
`PATH_VERSION`); a new order or a dropped step is a new path version, and the session
records say which version every verification used.

Each step reads program facts that already exist elsewhere (snapshot, FMZ facts, map,
this-expiry walls, events) and says them in a fixed vocabulary. Nothing here weighs,
forecasts or decides; no model is called. D1 to Q1 never show a premium number.

Pure functions. The workbench builds the frame; the session store keeps the readings.
Design: docs/astra/DECISION_PATH_DESIGN_20261014.md.
"""
from __future__ import annotations

import math
import time
from typing import Any, Mapping, Sequence

SCHEMA = "astra_decision_path_reading@1.0.0"
PATH_VERSION = "dp-2026-10-14.1"
STEPS: tuple[dict[str, Any], ...] = (
    {"id": "D0", "title": "资料", "question": "能读吗？"},
    {"id": "D1", "title": "责任", "question": "这张保单承担什么？"},
    {"id": "D2", "title": "位置", "question": "现价离责任区在结构上多远，途中有什么？"},
    {"id": "D3", "title": "运动", "question": "正往哪动？会不会被放大？"},
    {"id": "D4", "title": "外部", "question": "这期哪些外部信息值得看？"},
    {"id": "D5", "title": "变化", "question": "怎么变到现在的？"},
    {"id": "Q1", "title": "风险质量", "question": "合起来风险是什么样？"},
    {"id": "Q2", "title": "风险补偿", "question": "给的钱够不够看？", "premium": True},
    # AU shows the model review and the inquirer's questions (docs/opus/14) and is marked like the others.
    {"id": "AU", "title": "审计", "question": "模型认同吗？还剩什么要你判断？", "on_demand": True, "markable": True},
)
# What the audit step holds in sessions started from now on; older sessions have no au_mode.
AU_MODE = "review_inquiry"
STEP_IDS = tuple(step["id"] for step in STEPS)
MARKS = ("toward_underwrite", "unchanged", "toward_decline")
CONCLUSIONS = ("lock", "decline", "watch")
BJT_MS = 8 * 3_600_000
QUOTE_OLD_MS = 120_000
TRANSITION_WINDOW_MS = 24 * 3_600_000
# Codes that are permanent design facts, not missing data, for D0.
_D0_IGNORED = {"frozen_risk_reference_missing", "risk_reference_missing"}
_GAP_CN = {
    "quote": "报价缺口", "depth": "盘口深度不足", "fmz": "FMZ 事实不是当前", "walls": "本期墙暂缺",
    "walls_stale": "本期墙数据偏旧", "snapshot_gaps": "快照另有缺口", "quote_old": "报价超过 2 分钟",
    "snapshot": "没有冻结快照", "expired": "合约已到期",
}
# Walls older than this are left out of D2 (D0 already says they are stale).
WALLS_MAX_AGE_MS = 2 * 3_600_000
_REGIME = {"POSITIVE_GAMMA_PINNING": ("正 Gamma，波动倾向收敛", "ok"), "POSITIVE_GAMMA": ("正 Gamma，波动倾向收敛", "ok"),
           "NEGATIVE_GAMMA_AMPLIFYING": ("负 Gamma，波动倾向放大", "bad"), "NEGATIVE_GAMMA": ("负 Gamma，波动倾向放大", "bad"),
           "TRANSITION": ("翻转位附近，方向不稳定", "warn")}


# ---------------------------------------------------------------- helpers

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _get(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(key)
    return obj


def _usd(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:+.{digits}f}%"


def _bjt(ms: Any) -> str | None:
    if not isinstance(ms, int) or isinstance(ms, bool):
        return None
    return time.strftime("%m/%d %H:%M", time.gmtime((ms + BJT_MS) / 1000))


def _hours(ms: float | None) -> str:
    if ms is None:
        return "—"
    return f"{ms / 3_600_000:.1f} 小时"


def _line(label: str, text: str, tone: str = "") -> dict[str, str]:
    return {"k": label, "v": text, "tone": tone}


def _weak(state: Any) -> bool:
    """mild_* and neutral_to_* states lean only slightly; they are labelled as weak."""
    text = str(state or "").lower()
    return text.startswith("mild") or text.startswith("neutral_to")


def _lean(state: Any) -> int | None:
    """+1 up, -1 down, 0 none, None not ready (TMV / flow state words)."""
    if not isinstance(state, str) or not state:
        return None
    text = state.lower()
    if "bull" in text or text in {"up", "buy"}:
        return 1
    if "bear" in text or text in {"down", "sell"}:
        return -1
    return 0


# ---------------------------------------------------------------- frame

def contract_of(candidate: Mapping[str, Any]) -> dict[str, Any]:
    c = _get(candidate, "snapshot.contract") or {}
    return {"side": c.get("side") or candidate.get("side"), "candidate_id": c.get("candidate_id"),
            "short_strike_usd": _num(c.get("short_strike_usd")), "long_strike_usd": _num(c.get("long_strike_usd")),
            "expiry_ms": c.get("expiry_ms"), "short_instrument": c.get("short_instrument"),
            "long_instrument": c.get("long_instrument"), "quantity_btc": _num(c.get("quantity_btc")) or 1.0,
            "width_usd": _num(c.get("width_usd"))}


def _geometry(frame: Mapping[str, Any]) -> dict[str, Any]:
    cand = frame.get("candidate") or {}
    contract = contract_of(cand)
    side = contract["side"]
    spot = _num(_get(cand, "snapshot.market.reference_price_usd"))
    adverse = -1 if side == "put" else 1
    short = contract["short_strike_usd"]
    # positive: the short leg is outside the money by this many percent of spot
    dist = None if spot is None or short is None else (short - spot) / spot * 100 * adverse
    expiry = contract["expiry_ms"]
    at = frame.get("at_ms")
    hours = (expiry - at) / 3_600_000 if isinstance(expiry, int) and isinstance(at, int) else None
    walls = _usable_walls(frame)
    iv, iv_basis = _num(walls.get("atm_iv_pct")), "本期 ATM 标记 IV"
    if iv is None:
        iv, iv_basis = _term_iv(frame, contract, hours), "期限结构 ATM IV"
    move = iv * math.sqrt(hours / 8760) if iv is not None and hours is not None and hours > 0 else None
    return {"contract": contract, "side": side, "spot": spot, "adverse": adverse, "short": short,
            "long": contract["long_strike_usd"], "dist_pct": dist, "hours": hours, "iv": iv, "iv_basis": iv_basis,
            "move_pct": move, "ratio": dist / move if dist is not None and move else None}


def _usable_walls(frame: Mapping[str, Any]) -> dict[str, Any]:
    walls = frame.get("expiry_walls") or {}
    age = walls.get("age_ms")
    return {} if isinstance(age, (int, float)) and age > WALLS_MAX_AGE_MS else dict(walls)


def _term_iv(frame: Mapping[str, Any], contract: Mapping[str, Any], hours: float | None) -> float | None:
    info = _get(frame, "market_facts.space_context.gex_info") or {}
    token = str(contract.get("short_instrument") or "").split("-")
    token = token[1].upper() if len(token) > 1 else None
    rows = [r for r in info.get("term_structure") or [] if isinstance(r, Mapping) and _num(r.get("atm_iv")) is not None]
    exact = next((r for r in rows if token and str(r.get("expiry") or "").upper() == token), None)
    if exact:
        return _num(exact["atm_iv"])
    if rows and all(_num(r.get("days_to_expiry")) is not None for r in rows):
        days = hours / 24 if hours is not None and hours > 0 else 1.0
        return _num(min(rows, key=lambda r: abs(r["days_to_expiry"] - days))["atm_iv"])
    return _num(info.get("atm_iv"))


# ---------------------------------------------------------------- steps

def _d0(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    cand = frame.get("candidate") or {}
    snap = cand.get("snapshot")
    at = frame.get("at_ms")
    if not isinstance(snap, Mapping):
        return {"verdict": {"code": "stop", "cn": "不可核验", "tone": "bad"},
                "lines": [_line("快照", "没有冻结快照，无法核验", "bad")], "gaps": ["snapshot"]}
    expiry = g["contract"]["expiry_ms"]
    if isinstance(expiry, int) and isinstance(at, int) and at >= expiry:
        return {"verdict": {"code": "stop", "cn": "不可核验", "tone": "bad"},
                "lines": [_line("合约", "已到期", "bad")], "gaps": ["expired"]}
    quote = _get(snap, "market.quote") or {}
    gaps, lines = [], []
    valued = _get(snap, "market.valuation_ts_ms")
    age = at - valued if isinstance(valued, int) and isinstance(at, int) else None
    if quote.get("gap_reasons"):
        gaps.append("quote")
    depth = quote.get("available_depth_btc") or {}
    need = g["contract"]["quantity_btc"]
    if any((_num(depth.get(leg)) or 0) < need for leg in ("short", "long")):
        gaps.append("depth")
    if age is not None and age > QUOTE_OLD_MS:
        gaps.append("quote_old")
    lines.append(_line("报价", ("两腿盘口顺序估算" if quote.get("kind") == "sequential_leg_book_estimate" else "报价")
                       + (f"，{int(age / 1000)} 秒前" if age is not None else "，时间未知"),
                       "warn" if {"quote", "depth", "quote_old"} & set(gaps) else ""))
    fact_status = frame.get("fmz_status")
    if fact_status != "current":
        gaps.append("fmz")
    lines.append(_line("FMZ 事实", "当前" if fact_status == "current" else "不是当前版本", "" if fact_status == "current" else "warn"))
    walls = frame.get("expiry_walls")
    if not walls:
        gaps.append("walls")
        lines.append(_line("本期墙", "暂缺（不回退到全期限数值）", "warn"))
    elif walls.get("stale"):
        gaps.append("walls_stale")
        lines.append(_line("本期墙", f"数据 {int((walls.get('age_ms') or 0) / 60000)} 分钟前", "warn"))
    else:
        lines.append(_line("本期墙", f"{walls.get('expiry')}，{int((walls.get('age_ms') or 0) / 60000)} 分钟前"))
    other = [code for code in snap.get("gap_reasons") or [] if code not in _D0_IGNORED]
    if other:
        gaps.append("snapshot_gaps")
    verdict = ({"code": "partial", "cn": "部分可读", "tone": "warn"} if gaps
               else {"code": "readable", "cn": "可读", "tone": "ok"})
    if gaps:
        lines.append(_line("缺口", "、".join(_GAP_CN.get(code, code) for code in gaps), "warn"))
    return {"verdict": verdict, "lines": lines, "gaps": gaps, "snapshot_gap_codes": other}


def _d1(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    side, short, long_ = g["side"], g["short"], g["long"]
    name = "Put" if side == "put" else "Call"
    width = abs(short - long_) if short is not None and long_ is not None else None
    lines = [
        _line("方向", f"到期价{'跌破' if side == 'put' else '涨过'} {_usd(short)} 美元开始赔"),
        _line("责任区", f"{_usd(short)} → {_usd(long_)} 美元（宽 {_usd(width)} 美元）"),
        _line("最坏形状", "跌破保护腿后按 BTC 计赔付仍增加（币本位）" if side == "put"
              else "涨过保护腿后赔付封顶，不再增加"),
        _line("剩余", _hours(g["hours"] * 3_600_000 if g["hours"] is not None else None)),
    ]
    return {"verdict": {"code": "described", "cn": f"{name} 价差，持有到期", "tone": ""}, "lines": lines}


def _levels(frame: Mapping[str, Any], g: Mapping[str, Any]) -> list[dict[str, Any]]:
    usable = _usable_walls(frame)
    walls = usable.get("walls") or {}
    expiry = usable.get("expiry") or "本期"
    old = f"，{int(usable['age_ms'] / 60000)} 分钟前数据" if usable.get("stale") and isinstance(usable.get("age_ms"), (int, float)) else ""
    out = []
    own = "put" if g["side"] == "put" else "call"
    for key, label in ((f"gamma_{own}", f"本期 {own.capitalize()} Gamma 墙"), (f"oi_{own}", f"本期 {own.capitalize()} 持仓墙"),
                       ("pin", "本期 Pin")):
        price = _num(walls.get(key))
        if price is not None:
            out.append({"price": price, "label": f"{label} {_usd(price)}（{expiry}{old}）", "kind": key})
    flip = _num(_get(frame, "market_facts.space_context.anchor.effective_flip_point"))
    if flip is not None:
        out.append({"price": flip, "label": f"Flip {_usd(flip)}", "kind": "flip"})
    return out


def _d2(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    dist, ratio, move = g["dist_pct"], g["ratio"], g["move_pct"]
    if dist is None:
        buffer = {"code": "unknown", "cn": "未知", "tone": "warn"}
    elif dist < 0:
        buffer = {"code": "inside", "cn": "已进入", "tone": "bad"}
    elif ratio is None:
        buffer = {"code": "unknown", "cn": "未知", "tone": "warn"}
    elif ratio < 0.5:
        buffer = {"code": "narrow", "cn": "窄", "tone": "bad"}
    elif ratio < 1.0:
        buffer = {"code": "medium", "cn": "中等", "tone": "warn"}
    else:
        buffer = {"code": "wide", "cn": "宽", "tone": "ok"}
    if dist is None:
        text = f"{buffer['cn']}：距离未能计算"
    else:
        outward = "下方" if g["side"] == "put" else "上方"
        inward = "上方" if outward == "下方" else "下方"
        text = f"{buffer['cn']}：短腿在现价{outward if dist >= 0 else inward} {abs(dist):.2f}%"
        if ratio is not None and move is not None and g["iv"] is not None:
            text += f"，约 {ratio:.2f} 倍到期前预期波动（±{move:.2f}%，{g['iv_basis']} {g['iv']:.1f}%）"
    lines = [_line("缓冲", text, buffer["tone"])]
    spot, short = g["spot"], g["short"]
    on_way, beyond = [], []
    if spot is not None and short is not None:
        for level in _levels(frame, g):
            offset = (level["price"] - spot) * g["adverse"]
            reach = (short - spot) * g["adverse"]
            if 0 <= offset <= reach:
                on_way.append({**level, "offset": offset})
            elif offset > reach and level["kind"] != "flip":
                beyond.append({**level, "offset": offset})
    on_way.sort(key=lambda lv: lv["offset"])
    first = on_way[0] if on_way else None
    lines.append(_line("途中先遇到（本期口径）", f"{first['label']}，在现价{'下方' if g['adverse'] < 0 else '上方'} {first['offset'] / spot * 100:.2f}%"
                       if first else "现价到短腿之间没有本期墙、Pin 或 Flip"))
    if len(on_way) > 1:
        lines.append(_line("途中另有", "、".join(lv["label"] for lv in on_way[1:])))
    if beyond:
        lines.append(_line("短腿外侧", "、".join(lv["label"] for lv in sorted(beyond, key=lambda lv: lv["offset"])[:2])))
    regime = _get(frame, "market_facts.gamma_regime.regime")
    word, tone = _REGIME.get(regime, ("未知", "warn"))
    lines.append(_line("Gamma 体制", word, tone))
    sp = (frame.get("candidate") or {}).get("spatial_position") or {}
    at_short = _get(sp, "gamma.regime_at_short")
    if at_short in {"negative", "positive"}:
        lines.append(_line("短腿处", "负 Gamma（对冲顺势）" if at_short == "negative" else "正 Gamma（对冲逆势）",
                           "bad" if at_short == "negative" else "ok"))
    if sp.get("state") in {"favorable", "unfavorable"}:
        lines.append(_line("空间位置（周度 / 0DTE 墙口径，未验证）", ("相对有利" if sp["state"] == "favorable" else "相对不利")
                           + (f"：{sp['reason_cn']}" if sp.get("reason_cn") else ""),
                           "ok" if sp["state"] == "favorable" else "bad"))
    return {"verdict": buffer, "lines": lines, "first_on_way": first and first["kind"],
            "regime": word, "short_regime": at_short, "spatial": sp.get("state")}


def _d3(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    mf = frame.get("market_facts") or {}
    vp = mf.get("volume_price") or {}
    adverse = g["adverse"]

    def rel(lean: int | None, weak: bool) -> tuple[str, str]:
        if lean is None:
            return "未就绪", ""
        if lean == 0:
            return "无方向", ""
        word, tone = ("朝责任区", "bad") if lean == adverse else ("背离责任区", "ok")
        return (word + "（弱）", "warn" if tone == "bad" else "") if weak else (word, tone)

    reads = []
    for label, path in (("量价 24 小时", "tmvf_24h"), ("量价 48 小时", "tmvf_48h")):
        block = vp.get(path) or {}
        lean = _lean(block.get("final_state")) if block.get("data_ready") else None
        reads.append((label, lean, _weak(block.get("final_state"))))
    comb = _get(vp, "micro_flow.combined") or {}
    reads.append(("主动成交", _lean(comb.get("direction")) if comb.get("data_ready") else None, _weak(comb.get("state"))))
    mdie = mf.get("m_die") or {}
    no_move = mdie.get("level") == "NO_DIRECTIONAL_MOVE" or mdie.get("move_shape") == "NO_MOVE"
    mlean = 0 if no_move else {"UP": 1, "DOWN": -1}.get(mdie.get("direction"))
    reads.append(("近 15 分钟走势", mlean, False))
    lines = []
    toward = away = 0
    for label, lean, weak in reads:
        word, tone = rel(lean, weak)
        lines.append(_line(label, word, tone))
        if label != "近 15 分钟走势" and lean is not None and lean != 0:
            toward += lean == adverse
            away += lean == -adverse
    regime = _get(mf, "gamma_regime.regime")
    short_negative = _get(frame, "candidate.spatial_position.gamma.regime_at_short") == "negative"
    amplifying = str(regime or "").startswith("NEGATIVE") or short_negative
    if toward > away:
        verdict = ({"code": "toward_amplified", "cn": "朝责任区，易放大", "tone": "bad"} if amplifying
                   else {"code": "toward", "cn": "朝责任区", "tone": "warn"})
    elif away > toward:
        verdict = {"code": "away", "cn": "背离责任区", "tone": "ok"}
    else:
        verdict = {"code": "none", "cn": "无方向", "tone": ""}
    if amplifying:
        lines.append(_line("放大", "负 Gamma 下对冲顺势，若朝责任区移动会被放大"
                           + ("（短腿处为负 Gamma）" if short_negative else ""), "warn"))
    return {"verdict": verdict, "lines": lines, "toward": toward, "away": away}


def _d4(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    lines, listed = [], []
    events = _get(frame, "candidate.exogenous_events.events") or []
    for e in events:
        if not isinstance(e, Mapping):
            continue
        to_expiry = _num(e.get("to_expiry_ms"))
        text = f"{e.get('name_cn') or '宏观发布'}" + (f"（{e['period_cn']}）" if e.get("period_cn") else "")
        text += f"，北京时间 {_bjt(e.get('at_ms')) or '—'}" + (f"，距到期 {_hours(to_expiry)}" if to_expiry else "")
        lines.append(_line("事件", text + ("（日期待定）" if e.get("tentative") else ""), "warn"))
        listed.append("event")
    window = frame.get("cross_window") or {}
    if window.get("covers_us_session"):
        lines.append(_line("跨资产", "合约期内跨过美股交易时段（只作背景，不进入判断）"))
    regime = str(_get(frame, "market_facts.macro_pressure.macro_regime") or "")
    toward_put, toward_call = "Headwind" in regime, "Tailwind" in regime
    if (g["side"] == "put" and toward_put) or (g["side"] == "call" and toward_call):
        strength = "强" if "Strong" in regime else "温和"
        lines.append(_line("宏观", f"{strength}{'偏空（逆风）' if toward_put else '偏多（顺风）'}，朝 {'Put' if toward_put else 'Call'} 责任区", "warn"))
        listed.append("macro")
    calendar_gaps = _get(frame, "candidate.exogenous_events.gaps") or []
    if calendar_gaps:
        lines.append(_line("日历", "事件日历未完全覆盖本期（有日期待公布或日程未发布），未列出不等于没有", "warn"))
    if not listed:
        lines.append(_line("其余", "到期前没有需关注的外部项" if not calendar_gaps else "已知日程内没有需关注项"))
    verdict = ({"code": "listed", "cn": f"需关注 {len(listed)} 项", "tone": "warn"} if listed
               else {"code": "calendar_partial", "cn": "日历不完整", "tone": "warn"} if calendar_gaps
               else {"code": "none", "cn": "本期不必参考", "tone": "ok"})
    return {"verdict": verdict, "lines": lines, "listed": listed}


def _d5(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    vp = _get(frame, "market_facts.volume_price") or {}
    l24 = _lean(_get(vp, "tmvf_24h.final_state")) if _get(vp, "tmvf_24h.data_ready") else None
    l48 = _lean(_get(vp, "tmvf_48h.final_state")) if _get(vp, "tmvf_48h.data_ready") else None
    if l24 is None or l48 is None:
        pattern = {"code": "unknown", "cn": "未就绪", "tone": ""}
    elif vp.get("window_conflict") or (l24 and l48 and l24 != l48):
        pattern = {"code": "back_and_forth", "cn": "反复", "tone": "warn"}
    elif l24 and l24 == l48:
        pattern = {"code": "persistent", "cn": "持续", "tone": "bad" if l24 == g["adverse"] else ""}
    elif l24 and not l48:
        pattern = {"code": "recent", "cn": "新近", "tone": "warn" if l24 == g["adverse"] else ""}
    elif l48 and not l24:
        pattern = {"code": "fading", "cn": "消退", "tone": ""}
    else:
        pattern = {"code": "flat", "cn": "平稳", "tone": "ok"}
    lines = [_line("多窗口", f"24 小时{_lean_cn(l24)} / 48 小时{_lean_cn(l48)}：{pattern['cn']}", pattern["tone"])]
    at = frame.get("at_ms")
    recent = [t for t in frame.get("transitions") or [] if isinstance(t, Mapping)
              and isinstance(t.get("transition_ts_ms"), int) and isinstance(at, int)
              and 0 <= at - t["transition_ts_ms"] <= TRANSITION_WINDOW_MS]
    if recent:
        last = max(recent, key=lambda t: t["transition_ts_ms"])
        lines.append(_line("锚点效用变迁", f"近 24 小时 {len(recent)} 次，最近 {_bjt(last['transition_ts_ms'])}"
                           f"（{_STATE_CN.get(last.get('previous_state'), '前态')} → {_STATE_CN.get(last.get('current_state'), '现态')}）",
                           "warn" if len(recent) >= 3 else ""))
    else:
        lines.append(_line("锚点效用变迁", "近 24 小时没有变化"))
    return {"verdict": pattern, "lines": lines, "transitions_24h": len(recent)}


_STATE_CN = {"ANCHOR_EFFECTIVE": "锚点有效", "ANCHOR_INEFFECTIVE": "锚点失效",
             "STRUCTURE_ELIGIBLE": "结构放行", "STRUCTURE_BLOCKED": "结构关闭"}


def _lean_cn(lean: int | None) -> str:
    return {None: "未就绪", 0: "中性", 1: "偏多", -1: "偏空"}[lean]


def _q1(steps: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    items = []
    d0, d2, d3, d4, d5 = (steps[k] for k in ("D0", "D2", "D3", "D4", "D5"))
    if d0["verdict"]["code"] != "readable":
        items.append({"step": "D0", "text": f"资料{d0['verdict']['cn']}：" + "、".join(_GAP_CN.get(c, c) for c in d0.get("gaps") or [])})
    if d2["verdict"]["code"] in {"inside", "narrow"}:
        items.append({"step": "D2", "text": f"缓冲{d2['verdict']['cn']}：" + d2["lines"][0]["v"].split("：", 1)[-1]})
    if d3["verdict"]["code"] in {"toward", "toward_amplified"}:
        items.append({"step": "D3", "text": f"价格{d3['verdict']['cn']}"})
    if d4["verdict"]["code"] == "listed":
        items.append({"step": "D4", "text": "到期前有需关注的外部项：" + "；".join(
            line["v"].split("，北京时间")[0] for line in d4["lines"] if line["k"] in {"事件", "宏观"})})
    if d5["verdict"]["code"] in {"back_and_forth"} or (d5["verdict"]["code"] in {"persistent", "recent"} and d5["verdict"]["tone"]):
        items.append({"step": "D5", "text": f"多窗口方向{d5['verdict']['cn']}"})
    if d2.get("spatial") == "unfavorable" and not any(i["step"] == "D2" for i in items):
        items.append({"step": "D2", "text": "空间位置相对不利（程序，未验证）"})
    adverse = len(items)
    # first three by priority, shown in reading order
    items = sorted(items[:3], key=lambda i: STEP_IDS.index(i["step"]))
    verdict = ({"code": "attention", "cn": f"需关注 {adverse} 项" + ("（列出前 3 项）" if adverse > 3 else ""),
                "tone": "bad" if adverse >= 2 else "warn"} if adverse
               else {"code": "clear", "cn": "D1–D5 未见指向责任区的读数", "tone": "ok"})
    return {"verdict": verdict, "items": items, "adverse_count": adverse,
            "lines": [_line(i["step"], i["text"]) for i in items]}


def _q2(frame: Mapping[str, Any], g: Mapping[str, Any], q1: Mapping[str, Any]) -> dict[str, Any]:
    cand = frame.get("candidate") or {}
    eco = _get(cand, "snapshot.economics") or {}
    width = g["contract"]["width_usd"] or (abs(g["short"] - g["long"]) if g["short"] and g["long"] else None)
    net_usd, net_btc = _num(eco.get("net_credit_after_entry_fee_usd_at_spot")), _num(eco.get("net_credit_after_entry_fee_btc"))
    visible, spot = _num(eco.get("visible_credit_btc")), g["spot"]
    ratio = visible * spot / width * 100 if visible is not None and spot and width else None
    lines = [_line("净信用（扣入场费）", f"{_usd(net_usd)} 美元（{net_btc:.4f} BTC）" if net_usd is not None and net_btc is not None else "—"),
             _line("毛信用占宽度", f"{ratio:.1f}%" if ratio is not None else "—")]
    questions = {q.get("key"): q for q in _get(cand, "decision_reduction.questions") or [] if isinstance(q, Mapping)}
    worst = (questions.get("worst_case") or {}).get("numbers") or {}
    loss_btc, loss_usd = _num(worst.get("loss_at_protection_btc")), _num(worst.get("loss_at_protection_usd"))
    if loss_btc is not None:
        lines.append(_line("保护腿处净亏", f"{loss_btc:.4f} BTC（约 {_usd(loss_usd)} 美元）"
                           + ("，再往下按 BTC 计还会增加" if g["side"] == "put" else "，为最坏情况")))
    market = (questions.get("market_price") or {}).get("numbers") or {}
    edge, payout = _num(market.get("edge_usd")), _num(market.get("market_payout_usd"))
    if edge is not None and payout is not None:
        lines.append(_line("市场估价", f"按两腿标记价，到期平均赔付约 {_usd(payout)} 美元；净收比它"
                           f"{'多' if edge >= 0 else '少'} {_usd(abs(edge))} 美元",
                           "warn" if edge < 0 else ""))
        stance = {"code": "above_market" if edge >= 0 else "below_market",
                  "cn": "补偿不低于市场估价" if edge >= 0 else "补偿低于市场估价", "tone": "" if edge >= 0 else "warn"}
    else:
        stance = {"code": "market_missing", "cn": "市场估价缺失", "tone": "warn"}
    rv = frame.get("realized_vol") or {}
    iv = g["iv"]
    rv_end = rv.get("last_bar_end_ms")
    rv_age = frame["at_ms"] - rv_end if isinstance(rv_end, int) and isinstance(frame.get("at_ms"), int) else None
    rv24, rv7 = _num(rv.get("rv_24h_pct")), _num(rv.get("rv_7d_pct"))
    if iv is not None and (rv24 is not None or rv7 is not None):
        parts = [f"{g['iv_basis']} {iv:.1f}%"]
        if rv24 is not None:
            parts.append(f"近 24 小时实现 {rv24:.1f}%")
        if rv7 is not None:
            parts.append(f"近 7 天实现 {rv7:.1f}%")
        gap = iv - (rv24 if rv24 is not None else rv7)
        old = rv_age is not None and rv_age > 3 * 3_600_000
        lines.append(_line("隐含与实现波动", " · ".join(parts) + f"；IV {'高于' if gap > 0 else '低于' if gap < 0 else '等于'}近期实现 {abs(gap):.1f} 个点"
                           + (f"（实现波动截至 {_hours(rv_age)}前）" if old else ""),
                           "warn" if gap < 0 or old else ""))
    else:
        lines.append(_line("隐含与实现波动", "暂缺"))
    lines.append(_line("对照风险质量", f"{q1['verdict']['cn']}；以上补偿是否够看由你判断（不给概率）"))
    return {"verdict": stance, "lines": lines, "credit_to_width_pct": ratio, "edge_usd": edge}


def _failed(exc: Exception) -> dict[str, Any]:
    return {"verdict": {"code": "error", "cn": "读数失败", "tone": "warn"}, "error_type": type(exc).__name__,
            "lines": [_line("说明", "这一步读数失败，未影响其他步骤", "warn")]}


def build(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Readings of every step for one frame (start or now)."""
    g = _geometry(frame)
    steps: dict[str, Any] = {}
    for step_id, fn in (("D0", _d0), ("D1", _d1), ("D2", _d2), ("D3", _d3), ("D4", _d4), ("D5", _d5)):
        try:
            steps[step_id] = fn(frame, g)
        except Exception as exc:  # one step's bad input must not lose the others
            steps[step_id] = _failed(exc)
    try:
        steps["Q1"] = _q1(steps, g)
    except Exception as exc:
        steps["Q1"] = {**_failed(exc), "items": []}
    try:
        steps["Q2"] = _q2(frame, g, steps["Q1"])
    except Exception as exc:
        steps["Q2"] = _failed(exc)
    steps["AU"] = {"verdict": None, "lines": [], "note": "按需触发：模型只对照程序读数，不改数、不授权"}
    return {"schema": SCHEMA, "path_version": PATH_VERSION, "at_ms": frame.get("at_ms"),
            "snapshot_id": _get(frame, "candidate.snapshot.snapshot_id"),
            "spot_usd": g["spot"], "short_distance_pct": g["dist_pct"], "move_pct": g["move_pct"],
            "steps": steps}


def compare(start: Mapping[str, Any], now: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Which step verdicts changed between the start and the "now" reading."""
    out = []
    for step_id in STEP_IDS:
        a = _get(start, f"steps.{step_id}.verdict") or {}
        b = _get(now, f"steps.{step_id}.verdict") or {}
        if a.get("code") != b.get("code") and (a or b):
            out.append({"step": step_id, "from": a.get("cn"), "to": b.get("cn")})
    spot_a, spot_b = _num(start.get("spot_usd")), _num(now.get("spot_usd"))
    if spot_a and spot_b and spot_a != spot_b:
        out.append({"step": "spot", "from": _usd(spot_a), "to": _usd(spot_b)})
    return out


def steps_public() -> list[dict[str, Any]]:
    return [dict(step) for step in STEPS]


def validate_mark(step_id: Any, lean: Any) -> bool:
    return step_id in STEP_IDS and lean in MARKS


def markable(steps: Sequence[Mapping[str, Any]], step_id: str) -> bool:
    """Whether a session's step takes marks, by the steps the session started with: an
    on-demand step only when it says so (older sessions' audit step does not)."""
    step = next((s for s in steps or [] if isinstance(s, Mapping) and s.get("id") == step_id), None)
    if step is None:
        return False
    flag = step.get("markable")
    return flag if isinstance(flag, bool) else not step.get("on_demand")


def outcome(contract: Mapping[str, Any], entry_btc: float | None, payout_btc: float | None,
            delivery_usd: float | None) -> dict[str, Any] | None:
    """Static settlement result of a verified policy (locked or not), as the lock settlement does."""
    if entry_btc is None or payout_btc is None:
        return None
    result_btc = entry_btc - payout_btc
    return {"result_btc": result_btc, "result_usd": result_btc * delivery_usd if delivery_usd else None,
            "short_breached": None if delivery_usd is None or contract.get("short_strike_usd") is None else
            (delivery_usd < contract["short_strike_usd"] if contract.get("side") == "put"
             else delivery_usd > contract["short_strike_usd"])}


def summarize_marks(marks: Mapping[str, Any]) -> dict[str, str]:
    return {step: (marks.get(step) or {}).get("lean", "unchanged") for step in STEP_IDS}


def first_settled_step(marks: Mapping[str, Any], order: Sequence[str] = STEP_IDS) -> str | None:
    """After which step the lean stopped changing: the last step marked as moving the lean."""
    moved = [step for step in order if (marks.get(step) or {}).get("lean") in {"toward_underwrite", "toward_decline"}]
    return moved[-1] if moved else None
