"""Experience reading of the inquirer's questions (docs/opus/19, 2026-10-22).

Beside each question the page says how this kind of question is used given graded
experience (docs/opus/16, 17): the category, the basis with its grade, and, in the full view,
numbers of this contract that share the basis's measurement. Matching is fixed rules on the
question's text (astra_experience_cards_v1.json); nothing here enters the case file, L8 or the
inquirer's input, and the wording never argues for taking a contract.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_path_stats_v1 as path_stats
else:
    import astra_path_stats_v1 as path_stats

VERSION = "inquiry_lens@0.1"
CARDS_PATH = Path(__file__).with_name("astra_experience_cards_v1.json")
HOUR_MS = 3_600_000
MIN_SCORE = 3        # below this the match is a single weak word: say nothing
CLOSE_SHARE = 0.75   # another card scoring at least this share of the top one is shown beside it
MIN_PROMPT = (0, 4)


@lru_cache(maxsize=1)
def cards() -> dict[str, Any]:
    data = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    for card in data["cards"]:
        card["_re"] = [(re.compile(p), w) for p, w in card["match"]]
    data["_by_id"] = {c["id"]: c for c in data["cards"]}
    return data


def cards_digest() -> str:
    """Short hash of the card registry, recorded with the lens version (a card edit changes readings)."""
    import hashlib
    return hashlib.sha256(CARDS_PATH.read_bytes()).hexdigest()[:12]


def _asked(text: str) -> str:
    """The clause that actually asks: from the last colon or full stop before the last
    question mark (premises usually come first, the question last)."""
    end = text.rfind("？")
    head = text[:end] if end >= 0 else text
    cut = max(head.rfind("："), head.rfind("。"), head.rfind("；"))
    return text[cut + 1:] if cut >= 0 else text


def _scores(text: str) -> dict[str, int]:
    asked = _asked(text)
    out = {}
    for card in cards()["cards"]:
        score = sum(w * (len(r.findall(text)) + len(r.findall(asked))) for r, w in card["_re"])
        if score:
            out[card["id"]] = score
    return out


def classify(question: Mapping[str, Any]) -> dict[str, Any] | None:
    """The question's card by keyword score on its own text (the asked clause counts double);
    its reason is read only when the question matches nothing. A close second card is named in
    `also` (the question is about both); None when nothing scores MIN_SCORE (no guess)."""
    scores = _scores(str(question.get("question_cn") or "")) or _scores(str(question.get("why_material_cn") or ""))
    order = cards()["order"]
    ranked = sorted(scores, key=lambda c: (-scores[c], order.index(c)))
    if not ranked or scores[ranked[0]] < MIN_SCORE:
        return None
    card = cards()["_by_id"][ranked[0]]
    category = cards()["categories"][card["category"]]
    close = [c for c in ranked[1:] if scores[c] >= CLOSE_SHARE * scores[ranked[0]]][:1]
    return {"card": card["id"], "also": close, "also_cn": [cards()["_by_id"][c]["title_cn"] for c in close],
            "title_cn": card["title_cn"], "category": card["category"],
            "category_cn": category["label_cn"], "phrase_cn": category["phrase_cn"],
            "reading_cn": card["reading_cn"], "basis": card["basis"]}


# ── numbers of this contract (full view only) ──────────────────────────
def _entry(case: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    at = (case.get("refs") or {}).get(ref)
    if not at:
        return None
    try:
        return case["layers"][at["layer"]][at["index"]]
    except (KeyError, IndexError, TypeError):
        return None


def _values(case: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    entry = _entry(case, ref) or {}
    values = entry.get("values")
    return values if isinstance(values, Mapping) else {}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _utc(ms: float) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%m-%d %H:%M UTC")


def _daily_basis(case: Mapping[str, Any]) -> tuple[bool, str | None]:
    """doc 16/17 path statistics hold for an 08:00 UTC expiry with at most about a day left."""
    return path_stats.daily_basis(_values(case, "L6.session_clock"))


def _execution(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    comp = _values(case, "L2.compensation")
    ratio = _num(comp.get("net_credit_to_mark"))
    if ratio is not None:
        out.append({"label_cn": "扣费后净信用 ÷ 两腿 mark 价差", "value": f"{ratio:.0%}",
                    "note_cn": "前 14 张案卷的范围为 69–86%（未复核，不作基准）"})
    else:
        credit = _num(_values(case, "L1.economics").get("visible_credit_btc"))
        short_mark = _num(_values(case, "L2.book.short").get("mark_price"))
        long_mark = _num(_values(case, "L2.book.long").get("mark_price"))
        if credit is not None and short_mark is not None and long_mark is not None and short_mark - long_mark > 0:
            out.append({"label_cn": "可见信用 ÷ 两腿 mark 价差（扣费前）", "value": f"{credit / (short_mark - long_mark):.0%}",
                        "note_cn": "费用未知，扣费前的比例不能与 69–86% 比较"})
    book = _values(case, "L2.book.long")
    bid, ask = _num(book.get("best_bid_price")), _num(book.get("best_ask_price"))
    if bid is not None and ask is not None and ask > 0 and 0 <= bid <= ask:
        width = (ask - bid) / ((ask + bid) / 2)
        out.append({"label_cn": "保护腿盘口点差 ÷ 中间价", "value": "买价为 0（点差为中间价的 200%）" if bid == 0 else f"{width:.0%}",
                    "note_cn": "达到约 50% 时补偿读数要打折（17 号 §6 推论）" if width >= 0.5 else None})
    return out


def _side(case: Mapping[str, Any]) -> str | None:
    side = _values(case, "L1.contract").get("side")
    return side if side in ("put", "call") else None


def _session(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    clock = _values(case, "L6.session_clock")
    out = []
    for w in clock.get("windows") or []:
        if not isinstance(w, Mapping):
            continue
        at = _num(w.get("at_ms"))
        if at is not None:
            out.append({"label_cn": f"下一个高波动窗口：{w.get('label_cn') or ''}", "value": _utc(at),
                        "note_cn": "进行中" if w.get("in_progress") else None})
            break
    ok, why = _daily_basis(case)
    side, left = _side(case), _num(clock.get("hours_to_expiry"))
    row = path_stats.next_survival(left, side) if ok and side and left is not None else None
    if row:
        h, later, beyond = row
        out.append({"label_cn": f"若到 {h:02d}:00 UTC 仍未触及", "value": f"之后触及约 {later}%，到期越过约 {beyond}%",
                    "note_cn": path_stats.SURVIVAL_NOTE_CN})
    elif why:
        out.append({"label_cn": "未触及时的剩余风险", "value": "不给", "note_cn": why})
    warn = _entry(case, "L6.entry_warnings")
    out.append(_warnings(warn))
    return out


def _warnings(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    values = (entry or {}).get("values")
    if not isinstance(values, Mapping) or (entry or {}).get("quality") not in (None, "ok"):
        return {"label_cn": "不建仓警示（否决层）", "value": "不可用", "note_cn": None}
    items = [w for w in values.get("warnings") or [] if isinstance(w, Mapping)]
    fired = [str(w.get("label_cn") or w.get("code")) for w in items if w.get("triggered") is True]
    unknown = [w for w in items if w.get("triggered") is None]
    text = "、".join(fired) if fired else ("部分无法判断" if unknown else "未触发")
    return {"label_cn": "不建仓警示（否决层）", "value": text,
            "note_cn": "阈值未验证，只作否决；工作日 11:30–19:00 UTC（夏令时），标准 0.35Δ 短腿几乎必然触发“高波动窗口且缓冲不足”（17 号 §3.2）",
            "veto": True}


def _touch(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    ok, why = _daily_basis(case)
    if not ok:
        return [{"label_cn": "若此刻被触及", "value": "不给", "note_cn": why}]
    left = _num(_values(case, "L6.session_clock").get("hours_to_expiry"))
    if left is None:
        return []
    return [{"label_cn": f"若此刻被触及（剩余约 {left:.1f} 小时）", "value": path_stats.touch_text(left),
             "note_cn": path_stats.TOUCH_NOTE_CN}]


def _legs(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Where to watch: the contract's own legs (no reading of pin or acceleration, 17 号 §7.2)."""
    c = _values(case, "L1.contract")
    short, long_ = _num(c.get("short_strike_usd")), _num(c.get("long_strike_usd"))
    if short is None or long_ is None:
        return []
    return [{"label_cn": "关注价位", "value": f"短腿 {short:,.0f}；保护腿 {long_:,.0f}",
             "note_cn": "只记价位，不据此判断钉住或加速"}]


def _zero_dte(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    align = ((_entry(case, "L3.spatial_position") or {}).get("relations") or {}).get("expiry_alignment") or {}
    at = _num(align.get("zero_dte_expiry_ms"))
    if at is None:
        return [{"label_cn": "0DTE 结算", "value": "不给", "note_cn": "案卷 0.1.6 之前没有这项"}]
    before = align.get("zero_dte_expires_before_contract")
    return [{"label_cn": "0DTE 结算", "value": _utc(at),
             "note_cn": "早于本合约到期，结算后其墙位不再存在" if before else "即本合约到期日" if before is False else None}]


def _deep_tail(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = _num(_values(case, "L2.baseline.p_max_loss").get("value"))
    return [{"label_cn": "越过保护腿概率（市场隐含）", "value": f"{p:.1%}", "note_cn": "风险中性基线，不是真实世界概率"}] if p is not None else []


def _events(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = _values(case, "L7.events")
    events = values.get("events")
    gaps = values.get("gaps")
    out = [{"label_cn": "到期前事件", "value": f"{len(events)} 个" if isinstance(events, list) else "不可用",
            "note_cn": "事件日历有覆盖缺口" if gaps else None}]
    out.append(_warnings(_entry(case, "L6.entry_warnings")))
    return out


LIVE = {"execution": _execution, "gamma_pin": _legs, "session": _session, "touch_path": _touch, "zero_dte": _zero_dte,
        "deep_tail": _deep_tail, "events": _events}


def live(card: str, case: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Numbers of this contract for a card, only where the experience's measurement applies."""
    if not case or card not in LIVE:
        return []
    try:
        return [row for row in LIVE[card](case) if row]
    except Exception:   # a malformed case says nothing rather than something wrong
        return []


def applies_to(prompt_version: Any) -> bool:
    """Older sheets get no reading: on a blind holdout the rules placed 19 of 21 prompt-0.4
    questions right (18 after the review's rule fixes) but only 77–80% over all versions (docs/opus/19 §10; data in research_19/)."""
    m = re.fullmatch(r"inquiry_prompt@(\d+)\.(\d+)", str(prompt_version or ""))
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= MIN_PROMPT


def sheet_lens(sheet: Mapping[str, Any], case: Mapping[str, Any] | None = None, *,
               prompt_version: Any = None) -> dict[str, Any]:
    """Per question lens (with numbers when the case is given), counts by category, the
    reading order and the holding observation list."""
    if not applies_to(prompt_version):
        return {"version": VERSION, "enabled": False,
                "reason_cn": "这张提问单早于提示词 0.4，题目措辞不同，经验读法的规则没有在它上面验证过，不显示。"}
    per: dict[str, Any] = {}
    counts: dict[str, int] = {}
    observe: list[dict[str, Any]] = []
    for n, q in enumerate(sheet.get("questions") or [], 1):
        if not isinstance(q, Mapping):
            continue
        lens = classify(q)
        if lens is None:
            counts["unclassified"] = counts.get("unclassified", 0) + 1
            continue
        if case is not None:
            lens["live"] = live(lens["card"], case)
        per[str(q.get("id") or f"#{n}")] = lens
        counts[lens["category"]] = counts.get(lens["category"], 0) + 1
        if lens["category"] == "observe":
            # times and prices to watch; the entry veto's state is not one (it is shown on the question)
            points = [r for r in lens.get("live") or [] if r.get("value") not in ("不给", "不可用") and not r.get("veto")]
            same = next((o for o in observe if o["card"] == lens["card"] and o["points"] == points), None)
            if same:          # two questions of one card with the same watch points: one line
                same["question"] += "、" + str(q.get("id") or f"#{n}")
            else:
                observe.append({"question": str(q.get("id") or f"#{n}"), "card": lens["card"],
                                "title_cn": lens["title_cn"], "points": points})
    order = [c for c in ("execution", "observe", "not_a_reason", "scope", "declared_gap") if counts.get(c)]
    return {"version": VERSION, "enabled": True, "cards_schema": cards()["schema"], "questions": per, "counts": counts,
            "order": order, "observe": observe,
            "note_cn": "经验读法按题文自动归类（盲测中 0.4 提示词的题 21 道对 18 道，样本小，可能归错，请以卡片标题核对），只说明这类题怎么用，"
                       "不判断做或不做；观察点不是平仓点。"}
