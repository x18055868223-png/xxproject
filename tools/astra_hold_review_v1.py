"""Hold review: one model reading of a LOCKED policy while it is held (2026-10-14).

The program has already judged every lock-time reason (astra_thesis_ledger_v1). The model
only explains that judgement in a few short lines: what the reasons look like now, which
changes matter, and what to watch before expiry. It cannot change a state, give a
probability or suggest a trade. Pure functions; the workbench does the HTTP call.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping, Sequence

INPUT_SCHEMA = "hold_review_input@1.0.0"
RESULT_SCHEMA = "hold_review@1.0.0"
PROMPT_VERSION = "hold_review_prompt@1.0.0"
ANSWER_KEYS = {"summary", "points", "watch"}
MAX_POINTS, MAX_WATCH = 3, 3
SUMMARY_MAX, POINT_MAX, WATCH_MAX = 90, 70, 50
BJT_MS = 8 * 3_600_000
# Words that would turn an explanation into advice, a forecast or a trade.
BANNED = ("平仓", "加仓", "减仓", "止损", "止盈", "下单", "买入", "卖出期权", "建议买", "建议卖",
          "概率", "胜率", "必然", "一定会", "肯定会", "保证")
SIDE_CN = {"put": "Put", "call": "Call"}

SYSTEM_PROMPT = """你是承保工作台的持有中复核员。对象是一张已经锁定、持有到期的 BTC 垂直信用价差。
输入里的"依据账本"已由程序按固定规则判定：每条锁定时的依据现在是什么状态、期间经历了什么。
你的任务只是把它说清楚，不是重新判定。

规则：
1. 只使用输入中的事实和数字，不引入输入以外的数字、价格或事件。
2. 状态以账本为准，不得改判，不得说某条依据"其实没问题"或"其实已失效"。
3. 不给概率、胜率或预测，不给任何交易建议（平仓、加仓、止损等），页面不下单。
4. 收束：只写真正有变化、待定或需要盯住的内容；没变化的依据不要逐条复述。

只输出一个 JSON 对象，键恰好为：
- "summary"：一句话（不超过 90 字），说清当初的依据现在整体怎样。
- "points"：0–3 条，每条 {"thesis_id": 账本里的依据 id（如 "L1"）, "text": 不超过 70 字}，只写状态变化、待定读数或期间经历值得注意的依据。
- "watch"：0–3 条字符串，每条不超过 50 字，写到期前需要盯住的具体事项（例如输入里列出的事件时间、某条依据接近的阈值）。"""


def _bjt(ms: Any) -> str | None:
    if not isinstance(ms, int):
        return None
    return time.strftime("%m/%d %H:%M", time.gmtime((ms + BJT_MS) / 1000))


def _dur(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    minutes = int(round(ms / 60_000))
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小时" + (f" {rest} 分" if rest else "")


def build_input(ledger: Mapping[str, Any], contract: Mapping[str, Any], *, now_ms: int,
                events: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic, compact input: the ledger in words plus contract and calendar context."""
    projection = ledger.get("projection_cn") or {}
    theses = []
    for t in ledger.get("theses") or []:
        if t.get("state_now") == "NOT_RECORDED":
            continue
        item = {"id": t["id"], "依据": t.get("label_cn"), "锁定时": t.get("claim_cn"),
                "现在": t.get("state_now_cn"), "现值": t.get("now_value_cn"),
                "期间": t.get("experience_cn")}
        if t.get("pending_cn"):
            item["待定"] = t["pending_cn"]
        theses.append(item)
    expiry = contract.get("expiry_ms")
    upcoming = []
    for e in (events or {}).get("events") or []:
        upcoming.append({"事件": f"{e.get('name_cn')}（{e.get('period_cn')}）",
                         "北京时间": _bjt(e.get("at_ms")), "距到期": _dur(e.get("to_expiry_ms"))})
    return {
        "schema": INPUT_SCHEMA,
        "合约": {"方向": SIDE_CN.get(contract.get("side"), "价差"),
                 "卖出腿": contract.get("short_strike_usd"), "保护腿": contract.get("long_strike_usd"),
                 "到期北京时间": _bjt(expiry),
                 "剩余": _dur(expiry - now_ms) if isinstance(expiry, int) else None},
        "依据账本": {"概览": projection.get("headline_cn"), "位置": projection.get("where_cn"),
                     "节奏": projection.get("rhythm_cn") or [], "未知": projection.get("unknown_cn") or [],
                     "逐条": theses},
        "到期前事件": upcoming,
        "账本时刻": _bjt((ledger.get("coverage") or {}).get("latest_ms")),
    }


def input_hash(review_input: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(review_input, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def build_request(review_input: Mapping[str, Any], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(review_input, ensure_ascii=False, sort_keys=True)}],
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": 800,
    }


class HoldReviewInvalid(ValueError):
    pass


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _check_text(value: Any, limit: int, where: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HoldReviewInvalid(where + "_empty")
    text = value.strip()
    if len(text) > limit:
        raise HoldReviewInvalid(where + "_too_long")
    for word in BANNED:
        if word in text:
            raise HoldReviewInvalid(where + "_banned_word")
    # every number must come from the input (small counts like "3 条" are fine)
    for number in _NUMBER.findall(text):
        if number not in source and not (number.isdigit() and int(number) <= 6):
            raise HoldReviewInvalid(where + "_number_not_in_input")
    return text


def validate(answer: Any, review_input: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(answer, Mapping) or set(answer) != ANSWER_KEYS:
        raise HoldReviewInvalid("answer_keys")
    source = json.dumps(review_input, ensure_ascii=False)
    ids = {t["id"] for t in (review_input.get("依据账本") or {}).get("逐条") or []}
    summary = _check_text(answer["summary"], SUMMARY_MAX, "summary", source)
    points = answer["points"]
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        raise HoldReviewInvalid("points_shape")
    seen, clean_points = set(), []
    for point in points:
        if not isinstance(point, Mapping) or set(point) != {"thesis_id", "text"}:
            raise HoldReviewInvalid("point_keys")
        tid = point["thesis_id"]
        if tid not in ids or tid in seen:
            raise HoldReviewInvalid("point_thesis_id")
        seen.add(tid)
        clean_points.append({"thesis_id": tid, "text": _check_text(point["text"], POINT_MAX, "point", source)})
    watch = answer["watch"]
    if not isinstance(watch, list) or len(watch) > MAX_WATCH:
        raise HoldReviewInvalid("watch_shape")
    clean_watch = [_check_text(item, WATCH_MAX, "watch", source) for item in watch]
    return {"summary": summary, "points": clean_points, "watch": clean_watch}
