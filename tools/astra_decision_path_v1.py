"""Decision Path: the reading order used to verify one frozen policy (2026-10-14).

The order of the steps is the experiment. It is defined here only (`STEPS`,
`PATH_VERSION`); a new order or a dropped step is a new path version, and the session
records say which version every verification used.

Each step reads program facts that already exist elsewhere (snapshot, FMZ facts, map,
this-expiry walls, events) and says them in a fixed vocabulary. Nothing here weighs,
forecasts or decides; no model is called. D1 to Q1 never show a premium number (V's two price
cautions are the exception: they read P's reservation price).

Pure functions. The workbench builds the frame; the session store keeps the readings.
Design: docs/astra/DECISION_PATH_DESIGN_20261014.md; v2 (docs/opus/20, 2026-10-26) re-bases the
steps on the graded experience of docs/opus/16-17: one positive judgement only (Q2 price and
execution), everything else describes or vetoes; time (T) and the holding plan (H) are new;
direction readings are one line, same-source, never votes. Each line may carry `basis`
(grade and source); X/C readings and "no veto" never take the green tone.

dp-2026-10-27.1 is the minimal decision chain: the user reads and marks four composite steps,
V (unsuitable situations, astra_unsuitable_v1), R (the risk to carry), P (the price worth
taking, with the reservation price) and PL (the plan when touched), then AU. The components
D0-H are still read into every reading (case files, q1_items, sessions of older versions) and
each composite line keeps `src`, the component it came from.
"""
from __future__ import annotations

import math
import time
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_path_stats_v1 as path_stats
    from . import astra_unsuitable_v1 as unsuitable
else:
    import astra_path_stats_v1 as path_stats
    import astra_unsuitable_v1 as unsuitable

SCHEMA = "astra_decision_path_reading@2.0.0"
PATH_VERSION = "dp-2026-10-27.1"   # minimal decision chain V R P PL (components D0-H still read)
STEPS: tuple[dict[str, Any], ...] = (
    {"id": "V", "title": "否决", "question": "有没有不适合做的理由？", "veto_summary": True},
    {"id": "R", "title": "风险", "question": "要扛多少、什么时候来？"},
    {"id": "P", "title": "价格", "question": "什么价格才值得做？", "premium": True, "conclusion_gate": True},
    {"id": "PL", "title": "预案", "question": "被触及时你会怎么做？"},
    # AU shows the model review and the inquirer's questions (docs/opus/14) and is marked like the others.
    {"id": "AU", "title": "审计", "question": "模型认同吗？还剩什么要你判断？", "on_demand": True, "markable": True},
)
# The steps of dp-2026-10-26.3 (docs/opus/20, 22): still read as components of every reading;
# sessions of that version keep these in their own `steps`.
STEPS_V2: tuple[dict[str, Any], ...] = (
    {"id": "D0", "title": "资料", "question": "能读吗？"},
    {"id": "D1", "title": "责任", "question": "这张保单承担什么？"},
    {"id": "T", "title": "时间", "question": "现在承担的是哪段时间的风险？"},
    {"id": "D2", "title": "位置", "question": "离责任区多远（按本期预期波动）？"},
    {"id": "D3", "title": "运动", "question": "是否正朝短腿快速移动？"},
    {"id": "D4", "title": "事件", "question": "持有期内有哪些外生窗口？"},
    {"id": "Q1", "title": "否决汇总", "question": "有没有不做的理由？"},
    {"id": "Q2", "title": "价格与执行", "question": "给的钱够不够？能不能成交到？", "premium": True},
    {"id": "H", "title": "持有计划", "question": "锁定后怎么盯？"},
    # AU shows the model review and the inquirer's questions (docs/opus/14) and is marked like the others.
    {"id": "AU", "title": "审计", "question": "模型认同吗？还剩什么要你判断？", "on_demand": True, "markable": True},
)
# What the audit step holds in sessions started from now on; older sessions have no au_mode.
AU_MODE = "review_inquiry"
STEP_IDS = tuple(step["id"] for step in STEPS)
COMPONENT_IDS = tuple(step["id"] for step in STEPS_V2 if step["id"] != "AU")
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
# D2: a standard 0.35Δ short leg sits near 0.4 expected moves (docs/opus/17 §3.2); "narrow" is
# clearly closer than that (unvalidated, veto only). One constant, defined in astra_unsuitable_v1.
NARROW_MOVES = unsuitable.NARROW_MOVES
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


def _line(label: str, text: str, tone: str = "", basis: str | None = None) -> dict[str, str]:
    out = {"k": label, "v": text, "tone": tone}
    if basis:
        out["basis"] = basis
    return out


# grades and sources of the readings (docs/opus/16, 17), shown under each line
B_CLOCK = "A · 17 号 §2.1（风险按时段分布）"
B_ENTRY_HOUR = "B · 17 号 §3.1 / 附录 A（只有“16:00 优于 08:00”达到 B；其余时点与 08:00 相近；扣成本后的绝对优势取决于成本）"
B_VETO = "未验证，只作否决 · 13 号 / 17 号 §3.2（期限倒挂、保费突然变厚）"
B_SAME_SOURCE = "A/C · 17 号 §2.4（方向不能逐日外推；趋势与成交类读数多为同源）"
B_UNSIGNED = "口径 · 17 号 §7.2（GEX 不带符号，无法判定钉住还是加速）"
B_SPATIAL_X = "X · 17 号 §3.4（空间位置不能当缓冲）"
B_EVENTS = "B · 17 号 §2.2（公开事件已计入一天期报价；落在持有期内即为高波动窗口）"
B_EXECUTION = "B/C · 17 号 §6（优势与成本同量级；挂单接近中间价是唯一不依赖预测的改进，可成交性未验证）"
B_IV_RV = "X · 16 号第 4 条（“隐含低于实现 = 危险”已撤回；一天期 IV 与实现之差没有检验过的结论）"
B_EARLY_TOUCH = "A（方向）/ C（幅度）· 17 号 §4.2"
B_STATE = "回测支持（边缘，t 2.56）、未前向验证 · 21 号 §6"
B_BUSY_NOTE = "回测 · 22 号 §2.3"
# entry_warnings@0.1 vetoed a busy window with a thin cushion; @0.2 (docs/opus/22) made it a
# holding note. Blocks still carrying the old code (cached views, sessions) read it as the note.
LEGACY_BUSY_CODE = "busy_window_thin_cushion"
# docs/opus/21 §6: four states from realized-vol direction and implied minus realized; only one
# passed, as a timing interaction (16:00 beat 08:00 by +14.8, all cycles +8.8); no state's 08:00
# result differed from the rest
STATE_BACKTEST = {("扩张", "低于"): "此状态回测中 16:00 建仓比 08:00 多 +14.8（单位 1e-4 BTC，t 2.56，边缘；"
                                   "未证明大于其他状态，全体为 +8.8；一个半年反向）；08:00 本身与其他状态无差异",
                  ("扩张", "高于"): "此状态回测无显著差异", ("收敛", "高于"): "此状态回测无显著差异",
                  ("收敛", "低于"): "此状态回测无显著差异"}


def market_state(frame: Mapping[str, Any], g: Mapping[str, Any]) -> tuple[str, str] | None:
    """(S1, S2) of docs/opus/21: realized-vol direction (RV24 / RV7) and implied minus realized."""
    rv = frame.get("realized_vol") or {}
    rv24, rv7, iv = _num(rv.get("rv_24h_pct")), _num(rv.get("rv_7d_pct")), g.get("iv")
    if rv24 is None or rv7 is None or not rv7 or iv is None or g.get("iv_basis") != "本期 ATM 标记 IV":
        return None     # the registration needs this contract's own IV, not a term-structure fallback
    return ("扩张" if rv24 / rv7 >= 1 else "收敛"), ("高于" if iv - rv24 >= 0 else "低于")


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
    iv, code = resolve_iv(_usable_walls(frame), _get(frame, "market_facts.space_context.gex_info") or {}, contract, hours)
    iv_basis = IV_BASIS_CN.get(code)
    move = iv * math.sqrt(hours / 8760) if iv is not None and hours is not None and hours > 0 else None
    return {"contract": contract, "side": side, "spot": spot, "adverse": adverse, "short": short,
            "long": contract["long_strike_usd"], "dist_pct": dist, "hours": hours, "iv": iv, "iv_basis": iv_basis,
            "move_pct": move, "ratio": dist / move if dist is not None and move else None}


def _usable_walls(frame: Mapping[str, Any]) -> dict[str, Any]:
    walls = frame.get("expiry_walls") or {}
    age = walls.get("age_ms")
    return {} if isinstance(age, (int, float)) and age > WALLS_MAX_AGE_MS else dict(walls)


IV_BASIS_CN = {"deribit_mark_own_expiry": "本期 ATM 标记 IV", "gexmonitor_term": "期限结构 ATM IV",
               "gexmonitor_term_nearest": "期限结构 ATM IV", "gexmonitor_atm": "期限结构 ATM IV"}


def resolve_iv(walls: Mapping[str, Any], gex_info: Mapping[str, Any], contract: Mapping[str, Any],
               hours: float | None) -> tuple[float | None, str | None]:
    """The contract's ATM IV and its source code, one rule for the Decision Path and the page:
    this expiry's own Deribit mark IV (walls already passed the age rule), else the gexmonitor
    term row of this expiry, else the nearest term row, else gexmonitor's ATM IV."""
    iv = _num((walls or {}).get("atm_iv_pct"))
    if iv is not None and iv > 0:
        return iv, "deribit_mark_own_expiry"
    info = gex_info if isinstance(gex_info, Mapping) else {}
    token = str(contract.get("short_instrument") or "").split("-")
    token = token[1].upper() if len(token) > 1 else None
    rows = [r for r in info.get("term_structure") or [] if isinstance(r, Mapping) and _num(r.get("atm_iv")) is not None]
    exact = next((r for r in rows if token and str(r.get("expiry") or "").upper() == token), None)
    if exact:
        return _num(exact["atm_iv"]), "gexmonitor_term"
    if rows and all(_num(r.get("days_to_expiry")) is not None for r in rows):
        days = hours / 24 if hours is not None and hours > 0 else 1.0
        return _num(min(rows, key=lambda r: abs(r["days_to_expiry"] - days))["atm_iv"]), "gexmonitor_term_nearest"
    atm = _num(info.get("atm_iv"))
    return (atm, "gexmonitor_atm") if atm is not None else (None, None)


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
    fact_age = _num(frame.get("fmz_age_ms"))
    lines.append(_line("FMZ 事实", "当前" if fact_status == "current" else
                       "不是当前" + (f"（{fact_age / 3_600_000:.1f} 小时前）" if fact_age is not None else ""),
                       "" if fact_status == "current" else "warn"))
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
              else "涨过保护腿后按 USD 计封顶，按 BTC 计随价格上涨递减"),
        _line("剩余", _hours(g["hours"] * 3_600_000 if g["hours"] is not None else None)),
    ]
    return {"verdict": {"code": "described", "cn": f"{name} 价差，持有到期", "tone": ""}, "lines": lines}


def _t(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    clock = frame.get("session_clock") or {}
    lines = []
    phase = _get(clock, "phase.label_cn")
    ratio = _num(clock.get("hour_ratio_now"))
    hour = clock.get("now_hour_utc")
    if phase or ratio is not None:
        lines.append(_line("当前时段", (phase or "—") + (f"，本小时风险约为全天平均的 {ratio:.2f} 倍" if ratio is not None else ""),
                           "", B_CLOCK))
    daily, _why = path_stats.daily_basis(clock)
    better = False
    if daily and isinstance(hour, int) and not isinstance(hour, bool):
        better = 15 <= hour <= 17
        lines.append(_line("建仓时点", f"现在 {hour:02d} 时（UTC）："
                           + ("处在 16:00 前后的较好建仓时段（意义主要是避开较差时点）" if better
                              else "不在 16:00 前后的较好时段；其余时点与 08:00 相近"), "", B_ENTRY_HOUR))
    window = next((w for w in clock.get("windows") or []
                   if isinstance(w, Mapping) and _num(w.get("in_hours")) is not None), None)
    near = False
    if window:
        in_h = _num(window.get("in_hours"))
        near = bool(window.get("in_progress")) or in_h <= 2
        lines.append(_line("下一个高波动窗口", f"{window.get('label_cn') or '窗口'}，"
                           + ("进行中" if window.get("in_progress") else f"{in_h:.1f} 小时后"), "warn" if near else "", B_CLOCK))
    state = market_state(frame, g) if daily else None
    if state:
        lines.append(_line("本期状态", f"波动{state[0]}、隐含{state[1]}实现：{STATE_BACKTEST[state]}"
                           "（口径近似：永续 1 小时实现波动与本期 mark IV）", "",
                           B_STATE if state == ("扩张", "低于") else "回测无差异 · 21 号 §6"))
    factor = _num(clock.get("remaining_clock_factor"))
    if factor is not None:
        lines.append(_line("剩余时段", f"到期前剩下的时段约为全天平均忙碌度的 {factor:.2f} 倍", "", B_CLOCK))
    warn = _get(frame, "candidate.entry_warnings") or {}
    items = [w for w in warn.get("warnings") or [] if isinstance(w, Mapping)
             and w.get("code") != LEGACY_BUSY_CODE] if isinstance(warn, Mapping) else []
    fired = [w for w in items if w.get("triggered") is True]
    for w in items:
        status = "触发" if w.get("triggered") is True else "未触发" if w.get("triggered") is False else "无法判断"
        lines.append(_line("否决层", f"{w.get('label_cn') or w.get('code')}：{status}",
                           "bad" if w.get("triggered") is True else "", B_VETO))
    if not items:
        lines.append(_line("否决层", "不可用", "warn"))
    note = busy_note(warn)
    if note is not None:
        lines.append(_line("时段提示", note["reason_cn"] if note["active"] else "未触发" if note["active"] is False
                           else "无法判断", "warn" if note["active"] else "", B_BUSY_NOTE))
    if fired:
        verdict = {"code": "veto", "cn": f"否决层触发 {len(fired)} 项", "tone": "bad"}
    elif near:
        verdict = {"code": "window_near", "cn": "高波动窗口临近", "tone": "warn"}
    elif better:
        verdict = {"code": "better_hours", "cn": "较好建仓时段", "tone": ""}
    else:
        verdict = {"code": "ordinary", "cn": "未见时段否决", "tone": ""}
    return {"verdict": verdict, "lines": lines, "fired": [w.get("code") for w in fired], "better_hours": better,
            "market_state": "/".join(state) if state else None,
            "next_window": None if not window else {"label_cn": window.get("label_cn"), "in_hours": _num(window.get("in_hours")),
                                                    "in_progress": bool(window.get("in_progress")), "near": near}}


def busy_note(block: Any) -> dict[str, Any] | None:
    """The busy-window holding note of an entry-warnings block: {active, reason_cn}. An @0.1
    block's busy_window_thin_cushion is read as this note (window or busy hour), never a veto."""
    if not isinstance(block, Mapping):
        return None
    note = next((n for n in block.get("notes") or [] if isinstance(n, Mapping) and n.get("code") == "busy_window"), None)
    if note is not None:
        return {"active": note.get("active"), "reason_cn": str(note.get("reason_cn") or "")}
    old = next((w for w in block.get("warnings") or [] if isinstance(w, Mapping) and w.get("code") == LEGACY_BUSY_CODE), None)
    if old is None:
        return None
    ev = old.get("evidence") if isinstance(old.get("evidence"), Mapping) else {}
    window = ev.get("next_window") if isinstance(ev.get("next_window"), Mapping) else None
    active = True if window or ev.get("busy_now") or old.get("triggered") is True else False if old.get("triggered") is False else None
    if not active:
        return {"active": active, "reason_cn": ""}
    in_h = _num((window or {}).get("in_hours"))
    what = (f"{window.get('label_cn') or '窗口'}" + ("正在进行" if in_h is None or in_h <= 0 else f"约 {in_h:.1f} 小时后")
            if window else "当前处于高波动时段")
    return {"active": True, "reason_cn": f"{what}。在这类时点按标准短腿建仓，2 小时内触及短腿约 35%（其他时点约 26%）；"
                                          "这是持有中的盯盘提示，不是否决。"}


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
    elif ratio < NARROW_MOVES:
        buffer = {"code": "narrow", "cn": "窄", "tone": "bad"}
    elif ratio < 0.55:
        buffer = {"code": "standard", "cn": "标准附近", "tone": ""}
    else:
        buffer = {"code": "wide", "cn": "较远", "tone": ""}     # a distance, not a reason to take the contract
    if dist is None:
        text = f"{buffer['cn']}：距离未能计算"
    else:
        outward = "下方" if g["side"] == "put" else "上方"
        inward = "上方" if outward == "下方" else "下方"
        text = f"{buffer['cn']}：短腿在现价{outward if dist >= 0 else inward} {abs(dist):.2f}%"
        if ratio is not None and move is not None and g["iv"] is not None:
            text += f"，约 {ratio:.2f} 倍到期前预期波动（±{move:.2f}%，{g['iv_basis']} {g['iv']:.1f}%）"
    lines = [_line("缓冲", text, buffer["tone"],
                   "距离 · 本期 ATM IV 折算；标准 0.35Δ 短腿约 0.4 倍（17 号 §3.2）；窄（< 0.25 倍）只作否决，阈值未验证")]
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
    if first:
        text = (f"{first['label']}，在现价{'下方' if g['adverse'] < 0 else '上方'} {abs(first['offset']) / spot * 100:.2f}%"
                + (f"；另有 {'、'.join(lv['label'] for lv in on_way[1:])}" if len(on_way) > 1 else ""))
    else:
        text = "现价到短腿之间没有本期墙、Pin 或 Flip"
    lines.append(_line("途中价位（本期口径）", text, "", B_UNSIGNED))
    if beyond:
        lines.append(_line("短腿外侧", "、".join(lv["label"] for lv in sorted(beyond, key=lambda lv: lv["offset"])[:2]), "", B_UNSIGNED))
    regime = _get(frame, "market_facts.gamma_regime.regime")
    word = _REGIME.get(regime, ("未知", ""))[0]
    lines.append(_line("Gamma 体制（信号层）", word + "；只作描述，不判定钉住或加速", "", B_UNSIGNED))
    sp = (frame.get("candidate") or {}).get("spatial_position") or {}
    if sp.get("state") in {"favorable", "unfavorable"}:
        lines.append(_line("空间位置（周度 / 0DTE 墙）", "程序判读为" + ("相对有利" if sp["state"] == "favorable" else "相对不利")
                           + "；不作缓冲，也不计入否决", "", B_SPATIAL_X))
    at = frame.get("at_ms")
    recent = [t for t in frame.get("transitions") or [] if isinstance(t, Mapping)
              and isinstance(t.get("transition_ts_ms"), int) and isinstance(at, int)
              and 0 <= at - t["transition_ts_ms"] <= TRANSITION_WINDOW_MS]
    if recent:
        last = max(recent, key=lambda t: t["transition_ts_ms"])
        lines.append(_line("锚点效用变迁", f"近 24 小时 {len(recent)} 次，最近 {_bjt(last['transition_ts_ms'])}"
                           f"（{_STATE_CN.get(last.get('previous_state'), '前态')} → {_STATE_CN.get(last.get('current_state'), '现态')}）"))
    return {"verdict": buffer, "lines": lines, "first_on_way": first and first["kind"],
            "regime": word, "spatial": sp.get("state"), "transitions_24h": len(recent)}


def _d3(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    mf = frame.get("market_facts") or {}
    vp = mf.get("volume_price") or {}
    adverse = g["adverse"]

    def word(lean: int | None, weak: bool) -> str:
        if lean is None:
            return "未就绪"
        if lean == 0:
            return "无方向"
        return ("朝责任区" if lean == adverse else "背离责任区") + ("（弱）" if weak else "")

    parts = []
    for label, path in (("量价 24h", "tmvf_24h"), ("量价 48h", "tmvf_48h")):
        block = vp.get(path) or {}
        lean = _lean(block.get("final_state")) if block.get("data_ready") else None
        parts.append(f"{label} {word(lean, _weak(block.get('final_state')))}")
    comb = _get(vp, "micro_flow.combined") or {}
    lean = _lean(comb.get("direction")) if comb.get("data_ready") else None
    parts.append(f"主动成交 {word(lean, _weak(comb.get('state')))}")
    lines = [_line("方向读数（同源，不计票）", "、".join(parts) + "；不用于选边，也不当作远离责任区的缓冲", "", B_SAME_SOURCE)]
    mdie = mf.get("m_die") or {}
    no_move = mdie.get("level") == "NO_DIRECTIONAL_MOVE" or mdie.get("move_shape") == "NO_MOVE"
    mlean = 0 if no_move else {"UP": 1, "DOWN": -1}.get(mdie.get("direction"))
    lines.append(_line("近 15 分钟走势", word(mlean, False)))
    regime = _get(mf, "gamma_regime.regime")
    short_negative = _get(frame, "candidate.spatial_position.gamma.regime_at_short") == "negative"
    if str(regime or "").startswith("NEGATIVE") or short_negative:
        lines.append(_line("负 Gamma", "信号层读数为负 Gamma" + ("（短腿处）" if short_negative else "")
                           + "：若朝责任区移动可能被放大；只作说明", "", B_UNSIGNED))
    hours = g["hours"]
    early = mlean is not None and mlean == adverse and hours is not None and hours > 12
    if early:
        lines.append(_line("早段朝短腿", "剩余时间超过 12 小时，近 15 分钟正朝短腿移动：若早段被触及，约四分之一会走到保护腿",
                           "warn", B_EARLY_TOUCH + "；15 分钟走势本身没有检验过的预测力"))
        verdict = {"code": "toward_early", "cn": "早段朝短腿（说明，不计否决）", "tone": "warn"}
    else:
        verdict = {"code": "no_veto", "cn": "方向不作承保理由", "tone": ""}
    return {"verdict": verdict, "lines": lines}


def _d4(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    lines, listed = [], []
    events = _get(frame, "candidate.exogenous_events.events") or []
    for e in events:
        if not isinstance(e, Mapping):
            continue
        to_expiry = _num(e.get("to_expiry_ms"))
        text = f"{e.get('name_cn') or '宏观发布'}" + (f"（{e['period_cn']}）" if e.get("period_cn") else "")
        text += f"，北京时间 {_bjt(e.get('at_ms')) or '—'}" + (f"，距到期 {_hours(to_expiry)}" if to_expiry else "")
        lines.append(_line("事件窗口", text + ("（日期待定）" if e.get("tentative") else "") + "；看时段，不看方向", "warn", B_EVENTS))
        listed.append("event")
    window = frame.get("cross_window") or {}
    if window.get("covers_us_session"):
        lines.append(_line("跨资产", "合约期内跨过美股交易时段（只作背景，不进入判断）"))
    calendar_gaps = _get(frame, "candidate.exogenous_events.gaps") or []
    if calendar_gaps:
        lines.append(_line("日历", "事件日历未完全覆盖本期（有日期待公布或日程未发布），未列出不等于没有", "warn"))
    if not listed:
        lines.append(_line("其余", "到期前没有已知的外生事件" if not calendar_gaps else "已知日程内没有外生事件"))
    verdict = ({"code": "listed", "cn": f"持有期内事件 {len(listed)} 个", "tone": "warn"} if listed
               else {"code": "calendar_partial", "cn": "日历不完整", "tone": "warn"} if calendar_gaps
               else {"code": "none", "cn": "无已知事件", "tone": ""})
    return {"verdict": verdict, "lines": lines, "listed": listed}


_STATE_CN = {"ANCHOR_EFFECTIVE": "锚点有效", "ANCHOR_INEFFECTIVE": "锚点失效",
             "STRUCTURE_ELIGIBLE": "结构放行", "STRUCTURE_BLOCKED": "结构关闭"}


def _q1(steps: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    """Every veto, in reading order. No veto is said plainly and never as a reason to take it."""
    items = []
    d0, t, d2 = (steps[k] for k in ("D0", "T", "D2"))
    if d0["verdict"]["code"] != "readable":
        items.append({"step": "D0", "text": f"资料{d0['verdict']['cn']}：" + "、".join(_GAP_CN.get(c, c) for c in d0.get("gaps") or [])})
    if t["verdict"]["code"] == "veto":
        items.append({"step": "T", "text": t["verdict"]["cn"]})
    if d2["verdict"]["code"] in {"inside", "narrow"}:
        items.append({"step": "D2", "text": f"缓冲{d2['verdict']['cn']}：" + d2["lines"][0]["v"].split("：", 1)[-1]})
    verdict = ({"code": "veto", "cn": f"否决 {len(items)} 项", "tone": "bad"} if items
               else {"code": "no_veto", "cn": "未见否决项（这不是做的理由）", "tone": ""})
    lines = ([_line(i["step"], i["text"], "bad") for i in items] if items
             else [_line("结论", "未见否决项；是否做，看 Q2 的价格与执行")])
    return {"verdict": verdict, "items": items, "adverse_count": len(items), "lines": lines}


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
    market = (questions.get("market_price") or {}).get("numbers") or {}
    mark_btc = _num(market.get("market_payout_btc"))
    if mark_btc and mark_btc > 0:
        if net_btc is not None:
            lines.append(_line("净信用 ÷ 两腿 mark 价差", f"{net_btc / mark_btc:.0%}（前 14 张案卷范围 69–86%，未复核，不作基准）",
                               "", B_EXECUTION))
        elif visible is not None:
            lines.append(_line("可见信用 ÷ 两腿 mark 价差（扣费前）", f"{visible / mark_btc:.0%}（费用未知，不能与 69–86% 比较）",
                               "", B_EXECUTION))
    spread_cost = _num(market.get("spread_cost_usd"))
    if spread_cost is not None:
        lines.append(_line("成交口径成本", (f"按买卖价成交比按 mark 少收约 {_usd(abs(spread_cost))} 美元；"
                                            "挂单接近中间价可能收回一部分（可成交性未验证）") if spread_cost < 0
                           else f"按买卖价成交不低于按 mark（多 {_usd(spread_cost)} 美元）"))
    book = frame.get("long_book_top") or {}
    bid, ask = _num(book.get("bid")), _num(book.get("ask"))
    if ask is not None and ask > 0 and bid is not None and 0 <= bid <= ask:
        wide = (ask - bid) / ((ask + bid) / 2)
        lines.append(_line("保护腿盘口点差 ÷ 中间价", ("买价为 0（点差为中间价的 200%）" if bid == 0 else f"{wide:.0%}")
                           + ("；达到约 50%，补偿读数要打折" if wide >= 0.5 else ""), "warn" if wide >= 0.5 else "",
                           "推论（无等级）· 17 号 §6"))
    worst = (questions.get("worst_case") or {}).get("numbers") or {}
    loss_btc, loss_usd = _num(worst.get("loss_at_protection_btc")), _num(worst.get("loss_at_protection_usd"))
    if loss_btc is not None:
        lines.append(_line("保护腿处净亏", f"{loss_btc:.4f} BTC（约 {_usd(loss_usd)} 美元）"
                           + ("，再往下按 BTC 计还会增加" if g["side"] == "put" else "；按 USD 计封顶，按 BTC 计越过后递减"),
                           "", "B · 17 号 §2.3（一次大亏约抵四次全额收信用）"))
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
        old = rv_age is not None and rv_age > 3 * 3_600_000
        lines.append(_line("隐含与实现波动（只列数）", " · ".join(parts) + (f"（实现波动截至 {_hours(rv_age)}前）" if old else ""),
                           "warn" if old else "", B_IV_RV))
    else:
        lines.append(_line("隐含与实现波动", "暂缺"))
    lines.append(_line("对照否决汇总", f"{q1['verdict']['cn']}；补偿是否够、能否挂到接近中间价，由你判断（不给概率）"))
    return {"verdict": stance, "lines": lines, "credit_to_width_pct": ratio, "edge_usd": edge}


def _h(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    clock = frame.get("session_clock") or {}
    lines = []
    window = next((w for w in clock.get("windows") or []
                   if isinstance(w, Mapping) and isinstance(w.get("at_ms"), int) and not w.get("in_progress")), None)
    if window:
        lines.append(_line("检查点", f"{window.get('label_cn') or '高波动窗口'}（北京时间 {_bjt(window['at_ms'])}）之前检查一次"
                           "；窗口前后短腿更早被触及（在这类时点建仓，2 小时内触及约 35%，其他时点约 26%）", "",
                           f"{B_CLOCK}；{B_BUSY_NOTE}"))
    ok, why = path_stats.daily_basis(clock)
    left = _num(clock.get("hours_to_expiry"))
    row = path_stats.next_survival(left, g["side"]) if ok and left is not None else None
    if row:
        h, later, beyond = row
        lines.append(_line(f"若到 {h:02d}:00 UTC 仍未触及", f"之后触及约 {later}%，到期越过约 {beyond}%（{path_stats.SURVIVAL_NOTE_CN}）",
                           "", "B · 17 号 §4.1"))
    if ok and left is not None:
        lines.append(_line(f"若此刻被触及（剩余约 {left:.1f} 小时）", path_stats.touch_text(left), "", B_EARLY_TOUCH))
    elif why:
        lines.append(_line("路径统计", f"不给：{why}"))
    lines.append(_line("说明", "观察点不是平仓点；日内平仓规则约 20 个到期日后再议（17 号 §5）"))
    return {"verdict": {"code": "plan", "cn": "持有观察点", "tone": ""}, "lines": lines}


# ---------------------------------------------------------------- the decision chain (dp-2026-10-27.1)

_TONE_RANK = ("", "warn", "bad")


def _worst(*tones: Any) -> str:
    return max((t if t in _TONE_RANK else "" for t in tones), key=_TONE_RANK.index, default="")


def _src(line: Mapping[str, Any], src: str) -> dict[str, Any]:
    """A component's line as a composite line: unchanged, with the component it came from."""
    return {**dict(line), "src": src}


def _amount(btc: float | None, usd: float | None) -> str:
    return f"约 {_usd(abs(usd))} 美元" if usd is not None else f"{abs(btc):.4f} BTC" if btc is not None else "—"


def _reservation(frame: Mapping[str, Any], g: Mapping[str, Any]) -> dict[str, Any]:
    """The reservation price of the frame: the two legs' mark spread (decision_reduction R1, else the
    marks) plus the fee (credit minus net, as readings.pricing), against the capture's books."""
    cand = frame.get("candidate") or {}
    eco = _get(cand, "snapshot.economics") or {}
    credit = _num(eco.get("visible_credit_btc"))
    net = _num(eco.get("net_credit_after_fee_btc"))
    # all hold fees, never the entry fee alone (readings.pricing): an incomplete fee is unknown
    fee = credit - net if credit is not None and net is not None else _num(_get(cand, "snapshot.fee.amount_btc"))
    questions = {q.get("key"): q for q in _get(cand, "decision_reduction.questions") or [] if isinstance(q, Mapping)}
    mark = _num(((questions.get("market_price") or {}).get("numbers") or {}).get("market_payout_btc"))
    qty = g["contract"]["quantity_btc"]
    if mark is None:
        marks = cand.get("market_marks") or {}
        short_mark, long_mark = _num(marks.get("short_mark_btc")), _num(marks.get("long_mark_btc"))
        mark = (short_mark - long_mark) * qty if short_mark is not None and long_mark is not None else None
    return unsuitable.reservation(mark, fee, credit, frame.get("short_book_top"), frame.get("long_book_top"), qty, g["spot"])


def _mid_status(res: Mapping[str, Any]) -> str:
    if res.get("reservation_btc") is None:
        return "市场估价缺失"
    gap = res.get("mid_gap_btc")
    return "无盘口" if gap is None else "覆盖" if gap <= 0 else "不足"


def _v(frame: Mapping[str, Any], g: Mapping[str, Any], steps: Mapping[str, Any], res: Mapping[str, Any]) -> dict[str, Any]:
    """Every unsuitable situation (astra_unsuitable_v1), vetoes first, then one line listing every
    check and its status, so "no veto" is substantiated. No veto is never a reason to take it."""
    items: list[dict[str, Any]] = []
    checks = []
    d0 = steps.get("D0") or {}
    d0v = d0.get("verdict") or {}
    if d0v.get("code") != "readable":
        items.append({**unsuitable.data_item([_GAP_CN.get(c, c) for c in d0.get("gaps") or []] or ["资料读数失败"]),
                      "src": "D0"})
    checks.append(f"资料 {d0v.get('cn') or '未知'}")
    warn = unsuitable.warning_items(_get(frame, "candidate.entry_warnings"))
    items += [{**i, "src": "T"} for i in warn["items"]]
    checks += [f"{c['label_cn']} {c['status_cn']}" for c in warn["checks"]] or ["不建仓警示 不可用"]
    buffer = unsuitable.buffer_item(g["dist_pct"], g["ratio"], g["move_pct"], g["side"])
    if buffer:
        items.append({**buffer, "src": "D2"})
    checks.append(f"缓冲 {((steps.get('D2') or {}).get('verdict') or {}).get('cn') or '未知'}")
    for caution in (unsuitable.mid_item(res), unsuitable.long_book_item(res)):
        if caution:
            items.append({**caution, "src": "P"})
    checks.append(f"中间价对市场估价 {_mid_status(res)}")
    lines = [_src(_line(i["title_cn"], f"{i['evidence_cn']}。{i['why_cn']}；{i['clears_cn']}",
                        "bad" if i["kind"] == "veto" else "warn", i["basis"]), i["src"]) for i in items]
    lines.append(_src(_line("检查项", "；".join(checks)), "V"))
    vetoes = sum(i["kind"] == "veto" for i in items)
    cautions = len(items) - vetoes
    verdict = ({"code": "veto", "cn": f"不适合做：否决 {vetoes} 项", "tone": "bad"} if vetoes
               else {"code": "caution", "cn": f"提醒 {cautions} 项", "tone": "warn"} if cautions
               else {"code": "no_veto", "cn": "未见否决项（这不是做的理由）", "tone": ""})
    return {"verdict": verdict, "lines": lines, "items": items, "unknown_cn": warn["unknown_cn"]}


def _contract_lines(d1: Mapping[str, Any], g: Mapping[str, Any]) -> list[dict[str, Any]]:
    """D1 as one line: the liability zone, the worst shape and the time left; a width other than
    the usual 2,000 USD is flagged."""
    by = {l.get("k"): l.get("v") for l in d1.get("lines") or [] if isinstance(l, Mapping)}
    if not {"方向", "责任区"} <= set(by):          # a failed D1: its own lines
        return [_src(l, "D1") for l in d1.get("lines") or [] if isinstance(l, Mapping)]
    width = abs(g["short"] - g["long"]) if g["short"] is not None and g["long"] is not None else None
    odd = width is not None and abs(width - unsuitable.STANDARD_WIDTH_USD) > 0.5
    text = f"{by['方向']}；责任区 {by['责任区']}；最坏形状：{by.get('最坏形状') or '—'}；剩余 {by.get('剩余') or '—'}"
    if odd:
        text += f"；宽度不是常用的 {_usd(unsuitable.STANDARD_WIDTH_USD)} 美元"
    return [_src(_line("合约", text, "warn" if odd else ""), "D1")]


def _r(frame: Mapping[str, Any], g: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    """The risk to carry: the contract, then D2, T (without its veto layer, read in V), D4 and D3,
    each line as its component wrote it."""
    d1, t, d2, d3, d4 = (steps.get(k) or {} for k in ("D1", "T", "D2", "D3", "D4"))
    lines = _contract_lines(d1, g)
    for sid, step in (("D2", d2), ("T", t), ("D4", d4), ("D3", d3)):
        lines += [_src(l, sid) for l in step.get("lines") or []
                  if isinstance(l, Mapping) and not (sid == "T" and l.get("k") == "否决层")]
    buffer = d2.get("verdict") or {}
    parts = [f"缓冲{buffer.get('cn') or '未知'}"]
    window = t.get("next_window") or {}
    if window.get("near"):
        parts.append("窗口进行中" if window.get("in_progress") or window.get("in_hours") is None
                     else f"窗口 {window['in_hours']:.1f} 小时后")
    d4v = d4.get("verdict") or {}
    if d4v.get("code") == "listed":
        parts.append(f"事件 {len(d4.get('listed') or [])} 个")
    elif d4v.get("code") == "calendar_partial":
        parts.append("日历不完整")
    # a T veto is read in V, never here
    tone = _worst(buffer.get("tone"), "warn" if window.get("near") else "", d4v.get("tone"))
    return {"verdict": {"code": buffer.get("code") or "unknown", "cn": " · ".join(parts), "tone": tone}, "lines": lines}


def _p(frame: Mapping[str, Any], g: Mapping[str, Any], steps: Mapping[str, Any], res: Mapping[str, Any]) -> dict[str, Any]:
    """The price worth taking: Q2's lines, then the reservation price against the credit at the bid
    and at the two legs' mid, then the veto summary."""
    q2 = steps.get("Q2") or {}
    lines = [_src(l, "Q2") for l in q2.get("lines") or [] if isinstance(l, Mapping) and l.get("k") != "对照否决汇总"]
    reserve, visible, mid = res.get("reservation_btc"), res.get("visible_credit_btc"), res.get("mid_credit_btc")
    bid_gap, mid_gap = res.get("bid_gap_btc"), res.get("mid_gap_btc")
    if reserve is None:
        lines.append(_src(_line("保留价", "两腿标记价或费用缺失，保留价算不出", "warn", unsuitable.B_RESERVATION), "P"))
    else:
        lines.append(_src(_line("保留价", f"{reserve:.4f} BTC（{_amount(reserve, res.get('reservation_usd'))}）= 两腿标记价差 + 费用",
                                "", unsuitable.B_RESERVATION), "P"))
        if visible is not None:
            short = bid_gap > 0
            lines.append(_src(_line("现在可得", f"按买价两腿毛信用 {visible:.4f} BTC（{_amount(visible, res.get('visible_credit_usd'))}），"
                                    + (f"比保留价少{_amount(bid_gap, res.get('bid_gap_usd'))}" if short
                                       else f"不低于保留价（多{_amount(bid_gap, res.get('bid_gap_usd'))}）"),
                                    "warn" if short else ""), "P"))
        if mid is None:
            lines.append(_src(_line("两腿中间价", "缺对侧盘口，中间价不可算"), "P"))
        else:
            short = mid_gap > 0
            lines.append(_src(_line("两腿中间价", f"按两腿中间价毛信用 {mid:.4f} BTC（{_amount(mid, res.get('mid_credit_usd'))}），"
                                    + (f"比保留价少{_amount(mid_gap, res.get('mid_gap_usd'))}" if short
                                       else f"不低于保留价（多{_amount(mid_gap, res.get('mid_gap_usd'))}）"),
                                    "warn" if short else "", unsuitable.B_MID), "P"))
        if not res.get("reservation_reliable"):
            lines.append(_src(_line("保留价可靠性", "保护腿盘口空或过宽，标记价可能失真，保留价仅供参考", "warn",
                                    unsuitable.B_LONG_BOOK), "P"))
    v = (steps.get("V") or {}).get("verdict") or {}
    lines.append(_src(_line("对照否决", f"{v.get('cn') or '否决读数缺失'}；补偿是否够、挂在什么价，由你判断（不给概率）"), "V"))
    if reserve is None or (bid_gap is None and mid_gap is None):
        verdict = {"code": "market_missing", "cn": "市场估价缺失" if reserve is None else "现价信用缺失", "tone": "warn"}
    elif bid_gap is not None and bid_gap <= 0:
        verdict = {"code": "bid_covers", "cn": "按买价即不低于市场估价", "tone": ""}
    elif mid_gap is not None and mid_gap <= 0:
        verdict = {"code": "mid_covers", "cn": "若按中间价成交可覆盖市场估价", "tone": ""}
    elif mid_gap is not None:
        verdict = {"code": "mid_short", "cn": "中间价也低于市场估价", "tone": "warn"}
    else:
        verdict = {"code": "below_market", "cn": "按买价低于市场估价（中间价不可算）", "tone": "warn"}
    return {"verdict": verdict, "lines": lines, "reservation": dict(res), "reservation_btc": reserve,
            "mid_credit_btc": mid, "visible_credit_btc": visible,
            "credit_to_width_pct": q2.get("credit_to_width_pct"), "edge_usd": q2.get("edge_usd")}


def _pl(frame: Mapping[str, Any], g: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    """The plan when touched: H's holding checkpoints and path statistics."""
    lines = [_src(l, "H") for l in (steps.get("H") or {}).get("lines") or [] if isinstance(l, Mapping)]
    return {"verdict": {"code": "plan", "cn": "预案登记", "tone": ""}, "lines": lines}


def _failed(exc: Exception) -> dict[str, Any]:
    return {"verdict": {"code": "error", "cn": "读数失败", "tone": "warn"}, "error_type": type(exc).__name__,
            "lines": [_line("说明", "这一步读数失败，未影响其他步骤", "warn")]}


def build(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Readings of every step for one frame (start or now)."""
    g = _geometry(frame)
    steps: dict[str, Any] = {}
    for step_id, fn in (("D0", _d0), ("D1", _d1), ("T", _t), ("D2", _d2), ("D3", _d3), ("D4", _d4)):
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
    try:
        steps["H"] = _h(frame, g)
    except Exception as exc:
        steps["H"] = _failed(exc)
    try:
        res = _reservation(frame, g)
    except Exception:
        res = unsuitable.reservation(None, None, None)
    # V first: P's last line reads V's verdict
    for step_id, fn in (("V", lambda: _v(frame, g, steps, res)), ("R", lambda: _r(frame, g, steps)),
                        ("P", lambda: _p(frame, g, steps, res)), ("PL", lambda: _pl(frame, g, steps))):
        try:
            steps[step_id] = fn()
        except Exception as exc:
            steps[step_id] = {**_failed(exc), **({"items": []} if step_id == "V" else {})}
    steps["AU"] = {"verdict": None, "lines": [], "note": "按需触发：模型只对照程序读数，不改数、不授权"}
    return {"schema": SCHEMA, "path_version": PATH_VERSION, "at_ms": frame.get("at_ms"),
            "snapshot_id": _get(frame, "candidate.snapshot.snapshot_id"),
            "spot_usd": g["spot"], "short_distance_pct": g["dist_pct"], "move_pct": g["move_pct"],
            "steps": steps}


def compare(start: Mapping[str, Any], now: Mapping[str, Any], ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Which step verdicts changed between the start and the "now" reading, over `ids` (a
    session's own steps; this version's by default). Readings of two path versions compare
    only the steps both have, and say that the version changed."""
    out = []
    order = tuple(ids) if ids is not None else STEP_IDS
    if start.get("path_version") != now.get("path_version"):
        shared = set((start.get("steps") or {})) & set((now.get("steps") or {}))
        order = tuple(sid for sid in (order if ids is not None else (now.get("steps") or {})) if sid in shared)
        out.append({"step": "path_version", "from": start.get("path_version"), "to": now.get("path_version")})
    for step_id in order:
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


def summarize_marks(marks: Mapping[str, Any], order: Sequence[str] = STEP_IDS) -> dict[str, str]:
    """Marks of the steps a session has (its own path version's order)."""
    return {step: (marks.get(step) or {}).get("lean", "unchanged") for step in order}


def first_settled_step(marks: Mapping[str, Any], order: Sequence[str] = STEP_IDS) -> str | None:
    """After which step the lean stopped changing: the last step marked as moving the lean."""
    moved = [step for step in order if (marks.get(step) or {}).get("lean") in {"toward_underwrite", "toward_decline"}]
    return moved[-1] if moved else None
