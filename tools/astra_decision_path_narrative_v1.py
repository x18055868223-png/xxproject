"""Decision Path narrative view (docs/opus/24, prototype, 2026-10-27).

Turns one Decision Path reading (tools/astra_decision_path_v1.py) into short paragraphs by
fixed templates: no model, nothing added, nothing dropped. Every line of a step goes either
into a sentence or, when its basis is graded X or C, into a folded background sentence; the
basis strings become numbered footnotes. Each step also gets one question whose options map
to exactly one of the three leans (dp.MARKS), and a graded evidence list the user may cite.
Evidence grades are honest (docs/opus/17, 21, 22, 24 §3): direction and pin-side readings
are listed when relevant, always as "研究不支持作为依据", never as support.

Computed when a session is served, never stored; the path version is unchanged.
The composite steps of dp-2026-10-27.1 (V R P PL) read each line by its component's (`src`)
template and grading, and offer their components' evidence.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_decision_path_v1 as dp
else:  # direct CLI execution from tools/
    import astra_decision_path_v1 as dp

SCHEMA = "dp_narrative@0.1"
GRADES = ("A", "B", "C", "X", "未检验")
VERDICTS = ("研究支持", "边缘", "候选，未验证", "研究不支持作为依据")
DIRECTIONS = ("underwrite", "decline", "neutral")
BACKGROUND_PREFIX = {"X": "背景（研究不支持作为依据）：", "C": "背景（候选，未验证）："}
_TONES = ("", "warn", "bad")
_END = "。；，;,. "


# ---------------------------------------------------------------- line grades

def line_grade(basis: Any) -> str | None:
    """The grade a basis string starts with: "X · …" → X, "C · …" → C (alone; "A/C", "B/C"
    and "A（方向）/ C（幅度）" are not C alone), anything else → None (stays a sentence)."""
    if not isinstance(basis, str) or not basis.strip():
        return None
    head = basis.strip().split("·", 1)[0].strip()
    if head.startswith("X"):
        return "X"
    if head == "C":
        return "C"
    return None


def background_grade(step_id: str, line: Mapping[str, Any]) -> str | None:
    """Which background a line folds into: its basis grade (X, or C alone), and by key the
    readings research does not support as a basis (docs/opus/17 §2.4, 21 §6): the same-source
    direction line of D3, and T's market state when its backtest showed no difference (the
    marginal state stays in the paragraph)."""
    grade = line_grade(line.get("basis"))
    if grade:
        return grade
    key = line.get("k")
    step_id = str(line.get("src") or step_id)    # a composite line is graded as its component's
    if step_id == "D3" and key == "方向读数（同源，不计票）":
        return "X"
    if step_id == "T" and key == "本期状态" and str(line.get("basis") or "").startswith("回测无差异"):
        return "X"
    return None


# ---------------------------------------------------------------- sentence templates

def _sentence(text: str) -> str:
    return text.rstrip(_END) + "。"


def _veto_item(v: str) -> str:
    label, sep, status = v.partition("：")
    return f"{label}（{status}）" if sep else v


# step -> key -> (template, join group). Consecutive lines of the same group become one sentence.
Template = Callable[[str], str]
TEMPLATES: dict[str, dict[str, tuple[Template, str | None]]] = {
    "D0": {
        "快照": (lambda v: v, None),
        "合约": (lambda v: f"合约{v}", None),
        "报价": (lambda v: f"已有{v}" if v.startswith("报价") else f"报价为{v}", "src"),
        "FMZ 事实": (lambda v: "FMZ 事实是当前的" if v == "当前" else f"FMZ 事实{v}", "src"),
        "本期墙": (lambda v: f"本期墙{v}" if v.startswith(("暂缺", "数据")) else f"本期墙取自 {v}", "src"),
        "缺口": (lambda v: f"缺口有：{v}", None),
    },
    "D1": {
        "方向": (lambda v: v, "duty"),
        "责任区": (lambda v: f"责任区是 {v}", "duty"),
        "最坏形状": (lambda v: f"最坏的形状是：{v}", None),
        "剩余": (lambda v: f"距到期还剩 {v}", None),
    },
    "T": {
        "当前时段": (lambda v: f"现在是{v}", None),
        "建仓时点": (lambda v: f"从建仓时点看，{v}", None),
        "下一个高波动窗口": (lambda v: f"下一个高波动窗口是{v}", None),
        "本期状态": (lambda v: f"本期状态为{v}", None),
        "剩余时段": (lambda v: v, None),
        "否决层": (lambda v: "否决层不可用" if v == "不可用" else _veto_item(v), "veto"),
        "时段提示": (lambda v: f"时段提示{v}" if v in ("未触发", "无法判断") else f"时段提示：{v}", None),
    },
    "D2": {
        "缓冲": (lambda v: f"缓冲在{v}" if v.startswith("标准附近") else f"缓冲{v}", None),
        "途中价位（本期口径）": (lambda v: v if v.startswith("现价到短腿之间") else f"从现价到短腿之间：{v}", None),
        "短腿外侧": (lambda v: f"短腿外侧还有{v}", None),
        "Gamma 体制（信号层）": (lambda v: f"信号层的 Gamma 体制是{v}", None),
        "空间位置（周度 / 0DTE 墙）": (lambda v: f"周度 / 0DTE 墙构成的空间位置，{v}", None),
        "锚点效用变迁": (lambda v: f"锚点效用变迁{v}", None),
    },
    "D3": {
        "方向读数（同源，不计票）": (lambda v: f"方向读数彼此同源，不计票：{v}", None),
        "近 15 分钟走势": (lambda v: f"近 15 分钟走势{v}", None),
        "负 Gamma": (lambda v: v, None),
        "早段朝短腿": (lambda v: v, None),
    },
    "D4": {
        "事件窗口": (lambda v: f"持有期内的事件窗口：{v}", None),
        "跨资产": (lambda v: v, None),
        "日历": (lambda v: v, None),
        "其余": (lambda v: v, None),
    },
    "Q1": {
        "结论": (lambda v: v, None),
    },
    "Q2": {
        "净信用（扣入场费）": (lambda v: f"扣掉入场费后净收 {v}", "credit"),
        "毛信用占宽度": (lambda v: f"毛信用占价差宽度的 {v}", "credit"),
        "净信用 ÷ 两腿 mark 价差": (lambda v: f"净信用是两腿 mark 价差的 {v}", None),
        "可见信用 ÷ 两腿 mark 价差（扣费前）": (lambda v: f"扣费前的可见信用是两腿 mark 价差的 {v}", None),
        "成交口径成本": (lambda v: v, None),
        "保护腿盘口点差 ÷ 中间价": (lambda v: f"保护腿{v}" if v.startswith("买价") else f"保护腿盘口点差是中间价的 {v}", None),
        "保护腿处净亏": (lambda v: f"如果到期落在保护腿，净亏 {v}", None),
        "市场估价": (lambda v: v, None),
        "隐含与实现波动（只列数）": (lambda v: f"隐含与实现波动只列数：{v}", None),
        "隐含与实现波动": (lambda v: f"隐含与实现波动{v}", None),
        "对照否决汇总": (lambda v: f"对照否决汇总：{v}", None),
    },
    "H": {
        "检查点": (lambda v: f"在{v}", None),
        "路径统计": (lambda v: f"路径统计{v}", None),
    },
    # dp-2026-10-27.1: composite lines not listed here read by their component's (`src`) template;
    # V's unsuitable items read "title：evidence。why；clears"
    "V": {
        "检查项": (lambda v: f"逐项检查：{v}", None),
    },
    "R": {
        "合约": (lambda v: f"这张保单：{v}", None),
    },
    "P": {
        "保留价": (lambda v: f"保留价是 {v}", None),
        "现在可得": (lambda v: v, None),
        "两腿中间价": (lambda v: v, None),
        "保留价可靠性": (lambda v: v, None),
        "对照否决": (lambda v: f"对照否决：{v}", None),
    },
}
_COMMON = {"说明": (lambda v: v, None)}   # also the line of a failed step
# the step ids a Q1 veto line may be keyed by (every component of every path version)
_VETO_KEYS = frozenset(dp.COMPONENT_IDS) | {"D5"}


def _template(step_id: str, key: str, src: str | None = None) -> tuple[Template, str | None]:
    found = (TEMPLATES.get(step_id, {}).get(key) or (TEMPLATES.get(src, {}).get(key) if src else None)
             or _COMMON.get(key))
    if found:
        return found
    if "H" in (step_id, src) and key.startswith("若"):    # "若到 16:00 UTC 仍未触及", "若此刻被触及（…）"
        return (lambda v, k=key: f"{k}，{v}"), None
    if step_id == "Q1" and key in _VETO_KEYS:              # one veto item per line, keyed by its step
        return (lambda v, k=key: f"来自 {k} 的否决项：{v}"), None
    return (lambda v, k=key: f"{k}：{v}"), None


def _veto_join(parts: list[str]) -> str:
    return "否决层逐项看：" + "；".join(parts) if not (len(parts) == 1 and parts[0] == "否决层不可用") else parts[0]


# ---------------------------------------------------------------- leads

_LEAD_D2 = {"standard": "缓冲在标准附近。", "narrow": "缓冲窄。", "wide": "缓冲较远（这是距离，不是做的理由）。",
            "inside": "现价已越过短腿。", "unknown": "缓冲未知。"}


def _lead(step_id: str, verdict: Any) -> str:
    if not isinstance(verdict, Mapping) or not verdict.get("cn"):
        return ""
    cn, code = str(verdict["cn"]), verdict.get("code")
    if code == "error":
        return "这一步读数失败。"
    if step_id == "D2" and code in _LEAD_D2:
        return _LEAD_D2[code]
    if step_id == "D0":
        return _sentence(f"资料{cn}")
    if step_id == "D1":
        return _sentence(f"这是 {cn}")
    if step_id == "R":
        return _sentence("风险：" + cn.replace(" · ", "，"))
    return _sentence(cn)


# ---------------------------------------------------------------- asks

def _opt(key: str, label: str, lean: str, needs_note: bool = False) -> dict[str, Any]:
    return {"key": key, "label": label, "lean": lean, "needs_note": needs_note}


U, S, D = dp.MARKS  # toward_underwrite, unchanged, toward_decline

_ASKS: dict[str, dict[str, Any]] = {
    "D0": {"question": "资料够你判断吗？", "options": [_opt("enough", "够判断", S), _opt("gaps", "有缺口，让我犹豫", D)]},
    "D1": {"question": "这样的赔付形状，你能承担吗？",
           "options": [_opt("ok", "能承担", U), _opt("heavy", "偏重", D), _opt("same", "没影响", S)]},
    "T": {"question": "要扛过的这些时段，对你：",
          "options": [_opt("fine", "可以接受", U), _opt("hesitant", "让我更犹豫", D), _opt("same", "没影响", S)]},
    "D2": {"question": "这个缓冲距离，你觉得：",
           "options": [_opt("enough", "够用", U), _opt("close", "偏近", D), _opt("same", "没影响", S)]},
    "D3": {"question": "最近的走势，对你：",
           "options": [_opt("same", "不改变判断", S), _opt("hesitant", "让我更犹豫", D), _opt("keen", "让我更想做", U, True)]},
    "D4": {"question": "持有期的外生事件，对你：",
           "options": [_opt("fine", "可以接受", U), _opt("hesitant", "让我更犹豫", D), _opt("same", "没影响", S)]},
    "H": {"question": "这个盯盘安排，你做得到吗？",
          "options": [_opt("yes", "做得到", U), _opt("no", "做不到", D), _opt("same", "没影响", S)]},
    # dp-2026-10-27.1
    "R": {"question": "这些风险，你愿意扛吗？",
          "options": [_opt("accept", "愿意扛", U), _opt("heavy", "偏重", D), _opt("same", "没影响", S)]},
    # every answer is a plan, not a lean, except "cannot watch" (docs/opus/17 §5: no exit rule yet)
    "PL": {"question": "若持有中短腿被触及，你会：",
           "options": [_opt("hold", "持有到期", S), _opt("close", "触及即平仓", S), _opt("half", "平一半", S),
                       _opt("away", "这段时间盯不了", D)]},
}
# V and P: the question depends on the verdict
_ASK_V = {
    "veto": {"question": "这些否决项，你怎么处理？",
             "options": [_opt("stop", "按否决，不做", D), _opt("look", "仍想看价格（写下理由）", S, True)]},
    "caution": {"question": "这些提醒，对你：",
                "options": [_opt("noted", "知道了，继续", S), _opt("deter", "让我不想做", D)]},
    "error": {"question": "否决读数失败，请回看明细再继续。", "options": [_opt("go", "继续", S)]},
}
_ASK_P = {
    "price": {"question": "你打算怎么挂单？",
              # "below" keeps the old Q2 "有更好的理由" semantics: a price under the market's own estimate
              "options": [_opt("reserve", "按保留价或更高挂单", U), _opt("below", "低于保留价也愿意做（写下理由）", U, True),
                          _opt("pass", "价格不够，不做", D), _opt("later", "先不定", S)]},
    "market_missing": {"question": "市场估价或现价缺失，算不出保留价。你打算：",
                       "options": [_opt("below", "仍愿意做（写下理由）", U, True), _opt("pass", "价格无法判断，不做", D),
                                   _opt("later", "先不定", S)]},
    "error": {"question": "价格读数失败，请回看明细再判断。", "options": [_opt("go", "继续", S)]},
}


def _copy(ask: Mapping[str, Any]) -> dict[str, Any]:
    return {"question": ask["question"], "options": [dict(o) for o in ask["options"]]}


def _ask(step_id: str, step: Mapping[str, Any]) -> dict[str, Any] | None:
    code = (step.get("verdict") or {}).get("code") if isinstance(step.get("verdict"), Mapping) else None
    if step_id in _ASKS:
        return _copy(_ASKS[step_id])
    if step_id == "V":
        if code == "error" or not step.get("lines"):
            return _copy(_ASK_V["error"])
        return _copy(_ASK_V[code]) if code in _ASK_V else None     # no veto, no caution: no question
    if step_id == "P":
        if code == "error" or not step.get("lines"):
            return _copy(_ASK_P["error"])
        return _copy(_ASK_P["market_missing" if code == "market_missing" else "price"])
    if step_id == "Q1":
        if code == "veto" or step.get("items"):
            return {"question": "有否决项。", "options": [_opt("stop", "按否决，不做", D),
                                                       _opt("look", "仍想看价格（写下理由）", S, True)]}
        if code == "error":   # a failed summary is not "no veto"
            return {"question": "否决汇总读数失败，请回看前面各步再继续。", "options": [_opt("go", "继续看价格", S)]}
        return {"question": "没有否决项（这不是做的理由）。继续看价格。", "options": [_opt("go", "继续看价格", S)]}
    if step_id == "Q2":
        if code == "error" or not step.get("lines"):
            return {"question": "价格与执行读数失败，请回看前面各步再判断。", "options": [_opt("go", "继续", S)]}
        question = {"below_market": "补偿低于市场估价，你有比市场更有把握的理由吗？",
                    "above_market": "补偿不低于市场估价，你有比市场更有把握的理由吗？"}.get(
            code, "市场估价缺失，补偿无法与市场比较，你接受这个价格吗？")
        return {"question": question, "options": [_opt("yes", "有（写下理由）", U, True), _opt("no", "没有", D),
                                                  _opt("mid", "先挂中间价再看", S)]}
    return None   # AU and steps of other path versions


# Every option a step's question may offer (union of its variants): key -> (lean, needs_note).
# The store accepts a mark's choice only from this table, with the matching lean.
OPTIONS: dict[str, dict[str, tuple[str, bool]]] = {
    **{sid: {o["key"]: (o["lean"], o["needs_note"]) for o in ask["options"]} for sid, ask in _ASKS.items()},
    "Q1": {"stop": (D, False), "look": (S, True), "go": (S, False)},
    "Q2": {"yes": (U, True), "no": (D, False), "mid": (S, False), "go": (S, False)},
    **{sid: {o["key"]: (o["lean"], o["needs_note"]) for ask in asks.values() for o in ask["options"]}
       for sid, asks in (("V", _ASK_V), ("P", _ASK_P))},
}


def option_label(step_id: str, key: str) -> str | None:
    """The label an option key of a step is shown with (its first variant's)."""
    variants = [_ASKS[step_id]] if step_id in _ASKS else list({"V": _ASK_V, "P": _ASK_P}.get(step_id, {}).values())
    return next((o["label"] for ask in variants for o in ask["options"] if o["key"] == key), None)


# ---------------------------------------------------------------- evidence

_VERDICT_OF = {"A": "研究支持", "B": "研究支持", "C": "候选，未验证", "X": "研究不支持作为依据", "未检验": "研究不支持作为依据"}


def _ev(key: str, label: str, value: str | None, grade: str, direction: str, source: str,
        verdict_cn: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value, "grade": grade,
            "verdict_cn": verdict_cn or _VERDICT_OF[grade], "direction": direction, "source": source}


def _lines_of(steps: Mapping[str, Any], step_id: str) -> list[Mapping[str, Any]]:
    step = steps.get(step_id)
    lines = step.get("lines") if isinstance(step, Mapping) else None
    return [l for l in lines or [] if isinstance(l, Mapping)]


def _line(steps: Mapping[str, Any], step_id: str, *keys: str) -> Mapping[str, Any] | None:
    return next((l for l in _lines_of(steps, step_id) if l.get("k") in keys), None)


def _v(line: Mapping[str, Any] | None) -> str | None:
    return None if line is None else str(line.get("v") or "") or None


def _join_v(*lines: Mapping[str, Any] | None) -> str | None:
    found = [str(l.get("v")) for l in lines if l is not None and l.get("v")]
    return "；".join(found) or None


def _pin_item(steps: Mapping[str, Any]) -> dict[str, Any] | None:
    on_way, beyond = _line(steps, "D2", "途中价位（本期口径）"), _line(steps, "D2", "短腿外侧")
    if on_way is None and beyond is None:
        return None
    return _ev("pin_walls_side", "本期 Pin、墙在有利一侧（可能把价格留在远离短腿处）", _join_v(on_way, beyond), "未检验",
               "neutral", "17 号 §7.2（GEX 不带符号，无法判定钉住还是加速）")


def _direction_item(steps: Mapping[str, Any]) -> dict[str, Any] | None:
    line = _line(steps, "D3", "方向读数（同源，不计票）")
    if line is None:
        return None
    return _ev("direction_flow", "量价、主动成交朝有利方向（背离责任区）", _v(line), "X", "neutral",
               "17 号 §2.4（方向不逐日延续，A；量价与成交类读数同源，C）")


def _entry_hour_item(steps: Mapping[str, Any]) -> dict[str, Any] | None:
    line = _line(steps, "T", "建仓时点")
    if line is None:
        return None
    better = bool((steps.get("T") or {}).get("better_hours")) if isinstance(steps.get("T"), Mapping) else False
    return _ev("entry_hour_1600", "16:00 前后建仓较好（只限 08:00 交割；意义主要是避开较差时点）", _v(line), "B",
               "underwrite" if better else "neutral", "17 号 §3.1 / 附录 A")


def _state_item(steps: Mapping[str, Any]) -> dict[str, Any] | None:
    line = _line(steps, "T", "本期状态")
    if line is None:
        return None
    edge = str(line.get("basis") or "").startswith("回测支持")
    # only 扩张/低于 passed (as a timing interaction, marginal); the other states showed no difference
    return _ev("market_state", "本期状态（扩张、隐含低于实现时 16:00 建仓更好）", _v(line), "C" if edge else "X", "neutral",
               "21 号 §6（回测 t 2.56，边缘；未前向验证）", "边缘" if edge else None)


_PCT = re.compile(r"(\d+(?:\.\d+)?)%")


def _iv_rv_item(steps: Mapping[str, Any]) -> dict[str, Any] | None:
    line = _line(steps, "Q2", "隐含与实现波动（只列数）")
    if line is None:
        return None
    text = str(line.get("v") or "")
    direction = "neutral"
    iv_part, _, rest = text.partition(" · ")
    iv = _PCT.search(iv_part)
    rv = _PCT.search(rest.split("近 7 天")[0]) if "近 24 小时实现" in rest else None
    if iv and rv and float(iv.group(1)) - float(rv.group(1)) >= 8.5:
        direction = "decline"      # the C candidate: IV far above RV24 was worse (17 §3.3)
    return _ev("iv_vs_rv24", "隐含远高于近 24 小时实现时结果偏差（约 8.5 点以上）", text or None, "C", direction,
               "17 号 §3.3（t −1.7 至 −2.2，多重检验下不显著）")


def _evidence(step_id: str, steps: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The graded evidence a step offers. The composites (dp-2026-10-27.1) offer their components':
    P = Q2's, R = D2's, T's and D3's plus events, V = the cushion and the execution item."""
    if step_id in ("P", "R", "V"):
        parts = {"P": ("Q2",), "R": ("D2", "T", "D3", "Q2"), "V": ("D2", "Q2")}[step_id]
        keep = EVIDENCE_KEYS[step_id]
        out: dict[str, dict[str, Any]] = {}
        for sid in parts:
            for e in _evidence(sid, steps):
                if e["key"] in keep:
                    out.setdefault(e["key"], e)
        return list(out.values())
    items: list[dict[str, Any] | None] = []
    if step_id == "Q2":
        items = [
            _ev("execution_mid", "挂单接近中间价可收回一部分买卖价差（成本差 B；能否成交 C，未验证）",
                _v(_line(steps, "Q2", "成交口径成本")), "B", "underwrite", "17 号 §6"),
            _entry_hour_item(steps),
            _ev("events_priced", "公开事件已计入一天期报价（公开信息难成为卖方优势）",
                _join_v(*[l for l in _lines_of(steps, "D4") if l.get("k") == "事件窗口"]), "B", "neutral", "17 号 §2.2"),
            _iv_rv_item(steps),
            _state_item(steps),
            _direction_item(steps),
            _pin_item(steps),
            _ev("win_loss_asymmetry", "胜负不对称：一次大亏约抵四次全额收信用", _v(_line(steps, "Q2", "保护腿处净亏")),
                "B", "decline", "17 号 §2.3"),
        ]
    elif step_id == "D2":
        cushion = _line(steps, "D2", "缓冲")
        code = ((steps.get("D2") or {}).get("verdict") or {}).get("code") if isinstance(steps.get("D2"), Mapping) else None
        spatial = _line(steps, "D2", "空间位置（周度 / 0DTE 墙）")
        items = [
            None if cushion is None else _ev(
                "cushion_vs_standard", "缓冲按本期预期波动折算：标准 0.35Δ 短腿约 0.4 倍；窄（< 0.25 倍）只作否决，阈值未验证",
                _v(cushion), "C", "decline" if code in {"narrow", "inside"} else "neutral", "17 号 §3.2；22 号"),
            _pin_item(steps),
            None if spatial is None else _ev("spatial_position", "周度 / 0DTE 墙构成的空间位置当作缓冲", _v(spatial), "X",
                                             "neutral", "17 号 §3.4"),
        ]
    elif step_id == "D3":
        early = _line(steps, "D3", "早段朝短腿")
        move = _line(steps, "D3", "近 15 分钟走势")
        items = [
            _direction_item(steps),
            None if early is None else _ev("early_touch", "早段被触及后约四分之一走到保护腿（方向 A；幅度 C）", _v(early),
                                           "A", "decline", "17 号 §4.2"),
            None if move is None else _ev("move_15m", "近 15 分钟走势的预测力", _v(move), "未检验", "neutral",
                                          "20 号 §6.2（15 分钟走势本身没有检验过的预测力）"),
        ]
    elif step_id == "T":
        note = _line(steps, "T", "时段提示")
        items = [
            None if note is None else _ev("busy_window_note", "高波动时段建仓更快被触及（2 小时内约 35% 对 26%；单一市场回测）",
                                          _v(note), "B", "neutral", "22 号 §2.3（平均结果不更差；持有中的盯盘提示）"),
            _entry_hour_item(steps),
            _state_item(steps),
        ]
    return [item for item in items if item is not None]


# The evidence a step may offer (its catalogue): a note may cite only these keys.
EVIDENCE_KEYS: dict[str, frozenset[str]] = {
    "Q2": frozenset({"execution_mid", "entry_hour_1600", "events_priced", "iv_vs_rv24", "market_state",
                     "direction_flow", "pin_walls_side", "win_loss_asymmetry"}),
    "D2": frozenset({"cushion_vs_standard", "pin_walls_side", "spatial_position"}),
    "D3": frozenset({"direction_flow", "early_touch", "move_15m"}),
    "T": frozenset({"busy_window_note", "entry_hour_1600", "market_state"}),
}
# dp-2026-10-27.1: no evidence item exists for the entry warnings themselves (unvalidated vetoes);
# R also offers the events item, whose lines (D4) it reads
EVIDENCE_KEYS.update({
    "V": frozenset({"cushion_vs_standard", "execution_mid"}),
    "R": EVIDENCE_KEYS["T"] | EVIDENCE_KEYS["D2"] | EVIDENCE_KEYS["D3"] | {"events_priced"},
    "P": EVIDENCE_KEYS["Q2"],
})


# ---------------------------------------------------------------- one step

def _tone(*tones: Any) -> str:
    return max((t if t in _TONES else "" for t in tones), key=_TONES.index, default="")


def narrate_step(step_id: str, step: Any, steps: Mapping[str, Any]) -> dict[str, Any]:
    return _narrate_step(step_id, step, steps)[0]


def _narrate_step(step_id: str, step: Any, steps: Mapping[str, Any]) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """The step narrative and, per line index, the sentence or background part it ended in."""
    step = step if isinstance(step, Mapping) else {}
    lines = step.get("lines") if isinstance(step.get("lines"), list) else []
    footnotes: list[dict[str, Any]] = []
    numbers: dict[str, int] = {}

    def fn(basis: Any) -> list[int]:
        if not isinstance(basis, str) or not basis.strip():
            return []
        if basis not in numbers:
            numbers[basis] = len(numbers) + 1
            footnotes.append({"n": numbers[basis], "text": basis})
        return [numbers[basis]]

    sentences: list[dict[str, Any]] = []
    background: list[dict[str, Any]] = []
    covered: list[int] = []
    group: dict[str, Any] | None = None     # an open joined sentence: {name, parts, fn, tone, lines}
    where: dict[int, dict[str, Any]] = {}

    def flush() -> None:
        nonlocal group
        if group is not None:
            parts = group["parts"]
            text = _veto_join(parts) if group["name"] == "veto" else "，".join(p.rstrip(_END) for p in parts)
            sentences.append({"text": _sentence(text), "fn": sorted(set(group["fn"])), "tone": group["tone"],
                              "keys": [str(lines[i].get("k") or "") for i in group["lines"]]})
            for i in group["lines"]:
                where[i] = sentences[-1]
            group = None

    for index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            continue
        key, value = str(line.get("k") or ""), str(line.get("v") if line.get("v") is not None else "")
        template, name = _template(step_id, key, str(line.get("src") or "") or None)
        text = template(value)
        refs = fn(line.get("basis"))
        covered.append(index)
        grade = background_grade(step_id, line)
        if grade in BACKGROUND_PREFIX:
            flush()
            background.append({"text": BACKGROUND_PREFIX[grade] + _sentence(text), "fn": refs})
            where[index] = background[-1]
            continue
        tone = _tone(line.get("tone"))
        if name is not None and group is not None and group["name"] == name:
            group["parts"].append(text)
            group["fn"] += refs
            group["tone"] = _tone(group["tone"], tone)
            group["lines"].append(index)
            continue
        flush()
        if name is not None:
            group = {"name": name, "parts": [text], "fn": list(refs), "tone": tone, "lines": [index]}
        else:
            sentences.append({"text": _sentence(text), "fn": refs, "tone": tone, "keys": [key]})
            where[index] = sentences[-1]
    flush()
    return ({"lead": _lead(step_id, step.get("verdict")), "sentences": sentences, "background": background,
             "footnotes": footnotes, "ask": _ask(step_id, step), "evidence": _evidence(step_id, steps),
             "covered": covered}, where)


def _keyed(step: Any) -> list[tuple[int, str, str]]:
    lines = step.get("lines") if isinstance(step, Mapping) and isinstance(step.get("lines"), list) else []
    return [(i, str(l.get("k") or ""), str(l.get("v") if l.get("v") is not None else ""))
            for i, l in enumerate(lines) if isinstance(l, Mapping)]


# Readings that only move with the clock (a quote's age, the hours left, a window's countdown) are
# not changes of the market: the now block would otherwise mark every step as changed a minute later.
_CLOCK_ONLY = re.compile(r"\d+(?:\.\d+)?\s*(?:秒前|分钟前|小时前|小时后|分钟后)|(?:还剩|剩余|距到期)\s*\d+(?:\.\d+)?\s*小时"
                         r"|\d+(?:\.\d+)?\s*小时(?=[，。；）]|$)")


def _settled(text: Any) -> str:
    return _CLOCK_ONLY.sub("#", str(text or ""))


def changes_of(step_id: str, start_step: Any, now_step: Any, start_steps: Mapping[str, Any],
               now_steps: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What changed in one step from the start reading to now: lines paired by key, repeated
    keys (否决层) in order. changed / added carry the now text and footnote numbers; removed
    carries the start text after "不再有：". Background lines count like sentences. Changed and
    added come in now order, then removed in start order."""
    _, start_where = _narrate_step(step_id, start_step, start_steps)
    _, now_where = _narrate_step(step_id, now_step, now_steps)
    pending: dict[str, list[tuple[int, str]]] = {}
    for i, k, v in _keyed(start_step):
        pending.setdefault(k, []).append((i, v))
    out: list[dict[str, Any]] = []

    def add(kind: str, text: str, refs: list[int]) -> None:
        item = {"kind": kind, "text": text, "fn": list(refs)}
        if item not in out:          # two lines of one joined sentence report it once
            out.append(item)

    for i, k, v in _keyed(now_step):
        part = now_where.get(i) or {"text": "", "fn": []}
        if pending.get(k):
            _, old = pending[k].pop(0)
            if _settled(old) != _settled(v):
                add("changed", part["text"], part["fn"])
        else:
            add("added", part["text"], part["fn"])
    for i, _k, _v in _keyed(start_step):
        if any(i == j for rest in pending.values() for j, _ in rest):
            add("removed", "不再有：" + (start_where.get(i) or {"text": ""})["text"], [])
    return out


def narrate(reading: Any, start: Any = None) -> dict[str, Any] | None:
    """The narrative of one reading (start or now); None when there is no reading. With the
    start reading, every step of this (now) reading also gets `changes` against it."""
    if not isinstance(reading, Mapping):
        return None
    if isinstance(start, Mapping):
        out = narrate(reading)
        now_steps = reading.get("steps") if isinstance(reading.get("steps"), Mapping) else {}
        start_steps = start.get("steps") if isinstance(start.get("steps"), Mapping) else {}
        for sid, n in out["steps"].items():
            n["changes"] = changes_of(sid, start_steps.get(sid), now_steps.get(sid), start_steps, now_steps)
        return out
    steps = reading.get("steps") if isinstance(reading.get("steps"), Mapping) else {}
    return {"schema": SCHEMA, "steps": {str(sid): narrate_step(str(sid), step, steps) for sid, step in steps.items()}}
