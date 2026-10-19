"""Inquirer: the built-in LLM's fact brief and high-value questions (docs/opus/04, 2026-10-20).

The inquirer reads one case file (without L9) and returns inquiry_sheet@0.2: a layer-by-
layer fact brief plus grounded, discriminating questions for the external researcher.
It does not conclude, authorise, compute new numbers or give probabilities.

Validation is per item, not all-or-nothing: an unknown ref is removed from its item; an
item left without refs, a question without two hypotheses, or a closed enum violation
drops that item; numbers that cannot be traced to the cited entries are flagged, not
dropped. A sheet that does not parse, or lacks the fact brief, gets one repair attempt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

if __package__:
    from . import signal_llm_review as llm_core
else:
    import signal_llm_review as llm_core

PROTOCOL = "inquiry_sheet@0.2"
PROMPT_VERSION = "inquiry_prompt@0.1"
SCHEMA = "astra_inquiry_result@0.1"
BRIEF_LAYERS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8")
LAYER_STATUS = {"material", "quiet", "unknown"}
QUESTION_TYPES = {"conflict", "collinear", "temporal", "gap", "mechanism", "compensation", "scenario"}
BEARS_ON = {"touch_prob", "expiry_loss_prob", "tail_loss", "compensation"}
PRIORITIES = {"high", "medium", "low"}
ANSWER_SOURCES = {"case_file", "history", "external", "unobtainable"}
DIRECTIONS = {"raises", "lowers", "unclear"}
CROSS_KINDS = {"corroborate", "conflict", "collinear"}
ANOMALY_KINDS = {"conflict", "outlier", "gap", "shift"}
# Authorisation and self-made odds. Citing the program baseline (L2.baseline) is allowed;
# a probability the inquirer invents shows up as an untraced number instead.
FORBIDDEN = re.compile(r"(建议(下单|承保|开仓|买入|卖出)|应当(承保|下单|拒绝)|可以下单|允许(交易|下单)|胜率|期望值|正期望)")
NUMBER = re.compile(r"(?<![A-Za-z_.])-?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")
CODE_TOKEN = re.compile(r"(?<![0-9.])[A-Za-z_]+[0-9][A-Za-z0-9_.\-]*|\d{1,2}[A-Z]{3}\d{2}")

SYSTEM_PROMPT = """你是 xxproject 的证据提问者。研究对象是一张具体的 Deribit BTC 币本位垂直信用价差（1 BTC，持有到期，约 24 小时内到期）。
你的产出交给一位独立的外部研究员（自主 Agent，能联网、能计算、会逐条核查你的前提）。你的产出只有两件事：
一、把事实基础讲清楚（事实底稿）；二、有依据地提出高价值问题（问题单）。你提出的问题决定后续研究的上限。

你不做：不下结论、不判断该不该做、不授权、不计算新数字、不给任何概率或期望值。概率基线已由程序算好（L2.baseline），你只引用。

【事实底稿规则】
1. 按 L0 到 L8 逐层写，每层都要写：status 取 material（有实质信息）/quiet（无实质信息）/unknown（缺数据或质量不可用）。
2. 每条事实必须带 refs（案卷里的 ref）。数字取自案卷原值，为便于阅读可按显示精度四舍五入（如 0.6199、0.53%、81392），百分比可由小数乘 100 表示；不得换算成案卷里没有的新量。
3. 案卷中 quality 为 stale/missing/unknown 的条目，不得当作已知事实陈述，只能写“缺失/过期”。
4. 只陈述，不推断。推断一律放进问题单的假设里。
5. 写出跨层关系 cross_layer：哪些层互相印证（corroborate）、互相矛盾（conflict）、或其实同源（collinear，看 lineage）。

【高价值问题的五条判定（每题必须全部满足）】
- 有据：每个前提写成“陈述 + refs”，数字与案卷一致。
- 材料性：写清它如何影响这张保单的触界概率、到期亏损概率、深尾损失或补偿是否充分（bears_on）。
- 可判别：至少两个相互竞争的假设，每个写明机制、什么证据会支持、什么证据会推翻，以及它成立时风险升高还是降低。
- 可回答：写明答案可能在哪找（案卷内 / 历史 / 外部联网 / 无法获得）。
- 非冗余：不是事实底稿或 L8 程序读数的复述。L8 是可以被质疑的程序判读，与 L8 结论相同的问题不算关键问题；与 L8 冲突的证据优先提问。

【价值排序】priority 按“答案能在多大程度上让风险偏离市场基线”排序：能明显改变触界或亏损判断的为 high。

【问题类型】conflict（证据相反）、collinear（多个信号是否同一驱动）、temporal（持续还是噪声）、gap（缺失数据会否改变判断）、mechanism（框架外的机制或关联）、compensation（补偿与风险是否错配）、scenario（到期前什么路径会触发责任）。至少覆盖 3 种类型，不足时在 coverage_note_cn 说明原因。

【轻幻觉规则】
- 前提里的数字必须来自所引 ref 的原值（可四舍五入显示）。
- 外扩推断只能写成假设；通用市场机制（如做市商对冲）可以用，但要写明它是机制假设，不是已发生的事实。
- 不引入案卷外的数字。证据不足就写 unknown，不要猜。
- 币本位：赔付以 BTC 计，Put 跌破保护腿后 BTC 赔付随价格下降继续增加；USD 与 BTC 不混用。
- Q1（风险质量）与 Q2（风险补偿）不能互相替代；外部叙事不能覆盖合同事实。

【好问题与伪问题示例】
好：“现价距短腿 0.50%（L1.liability），本期 Put 方 Gamma 最大集中在保护腿 79500 附近（L3.expiry_walls），而 M-DIE 为 NO_MOVE（L5.m_die）。到期前若下探，79500 一带的对冲是会减速下跌（钉住）还是加速穿越？前者降低深尾损失，后者提高；判别证据是该位 OI 构成与做市商净 Gamma 符号（L3.gex.structure）及历史同类到期的交割分布（history）。”
好：“信用/mark 价差为 thin（L2.compensation），但 IV 分位与 RR skew（L4.iv）未显示 Put 侧溢价抬升：是我们成交口径吃亏，还是市场确实没给这一侧风险加价？前者只影响补偿，后者意味着风险可能被低估。”
伪：“短腿距现价 0.50%，是否较近？”（复述事实，无竞争假设）
伪：“宏观偏弱，BTC 会不会跌？”（与这张保单无连接、无判别证据）

只输出一个 JSON 对象，不要输出其他文字。"""

OUTPUT_SPEC = """输出格式（JSON）：
{
 "protocol": "inquiry_sheet@0.2",
 "case_id": "<案卷 case_id>",
 "fact_brief": {
   "layers": [{"layer": "L0", "status": "material|quiet|unknown",
               "facts": [{"text_cn": "...", "refs": ["L0.fmz_fact"]}],
               "relations_cn": ["..."], "quality_cn": "..."}],
   "cross_layer": [{"kind": "corroborate|conflict|collinear", "text_cn": "...", "refs": ["..."]}]
 },
 "anomalies": [{"id": "A1", "kind": "conflict|outlier|gap|shift", "text_cn": "...", "refs": ["..."]}],
 "questions": [{
   "id": "K1", "type": "conflict|collinear|temporal|gap|mechanism|compensation|scenario",
   "bears_on": ["touch_prob|expiry_loss_prob|tail_loss|compensation"],
   "priority": "high|medium|low",
   "question_cn": "...",
   "premises": [{"claim_cn": "...", "refs": ["..."]}],
   "why_material_cn": "...",
   "hypotheses": [{"h_cn": "...", "mechanism_cn": "...", "would_support_cn": "...", "would_refute_cn": "...",
                   "direction": "raises|lowers|unclear"}],
   "answer_source": "case_file|history|external|unobtainable",
   "from_anomalies": ["A1"]
 }],
 "coverage_note_cn": "..."
}
fact_brief.layers 必须恰好覆盖 L0–L8 九层。每个问题至少 2 个 hypotheses。按 priority 从高到低排列。"""


def assert_no_l9(case_file: Mapping[str, Any]) -> None:
    """The inquirer reads the current cross-section only; history (L9) never reaches it."""
    if "L9" in (case_file.get("layers") or {}) or any(
            str(r).startswith("L9.") for r in (case_file.get("refs") or {})):
        raise ValueError("the inquirer must not receive L9; use astra_case_file_v1.for_inquirer")


def build_request(case_file: Mapping[str, Any], model: str, *, effort: str = "high",
                  repair_note: str | None = None, previous: str | None = None) -> dict[str, Any]:
    assert_no_l9(case_file)
    case_json = json.dumps(case_file, ensure_ascii=False, separators=(",", ":"))
    user = OUTPUT_SPEC + "\n\n事实案卷（L0–L8，按层排列）：\n" + case_json
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    if repair_note:
        messages += [{"role": "assistant", "content": previous or "（上次输出为空）"},
                     {"role": "user", "content": "上面的输出未通过格式检查：" + repair_note +
                      "\n请只修正这些问题，重新输出完整的 JSON 对象。"}]
    return {"model": model, "messages": messages, "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"}, "reasoning_effort": effort, "max_tokens": 65536,
            "_local_prompt_version": PROMPT_VERSION}


def _entry_numbers(case_file: Mapping[str, Any], refs: list[str]) -> list[float]:
    found: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            found.append(float(value))
        elif isinstance(value, str):
            for token in NUMBER.findall(value):
                try:
                    found.append(float(token.replace(",", "")))
                except ValueError:
                    pass
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for ref in refs:
        at = (case_file.get("refs") or {}).get(ref)
        if at:
            entry = case_file["layers"][at["layer"]][at["index"]]
            walk(entry.get("values"))
            walk(entry.get("relations"))
    return found


def _untraced_numbers(text: str, case_file: Mapping[str, Any], refs: list[str]) -> list[str]:
    """Numbers in the text that match no number of the cited entries at the text's precision
    (also as a percentage of a fraction). Small integers (≤ 31) are ordinals, dates or counts."""
    stripped = CODE_TOKEN.sub(" ", text)
    pool = _entry_numbers(case_file, refs)
    bad = []
    for token in NUMBER.findall(stripped):
        raw = token.replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if raw.lstrip("-").isdigit() and abs(value) <= 31:
            continue
        mantissa = raw.lower().split("e")[0]
        decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
        exponent = int(raw.lower().split("e")[1]) if "e" in raw.lower() else 0
        tol = 0.5 * 10 ** (exponent - decimals) + 1e-9 * abs(value)
        if "." not in mantissa and "e" not in raw.lower() and abs(value) >= 100:
            zeros = len(mantissa) - len(mantissa.rstrip("0"))
            tol = max(tol, 0.5 * 10 ** zeros)   # "81,400" is a display rounding of 81391.6
        if not any(abs(abs(v) - abs(value)) <= tol or abs(abs(v) * 100 - abs(value)) <= tol for v in pool):
            bad.append(token)
    return bad


def validate(sheet: Any, case_file: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """Returns (cleaned sheet, issues, structural_error). structural_error triggers a repair."""
    issues: list[dict[str, Any]] = []
    if not isinstance(sheet, Mapping):
        return None, issues, "顶层不是 JSON 对象"
    assert_no_l9(case_file)
    known = set((case_file.get("refs") or {}).keys())
    brief = sheet.get("fact_brief")
    layers = (brief or {}).get("layers") if isinstance(brief, Mapping) else None
    if not isinstance(layers, list):
        return None, issues, "缺少 fact_brief.layers"
    seen = [l.get("layer") for l in layers if isinstance(l, Mapping)]
    missing = [L for L in BRIEF_LAYERS if L not in seen]
    if missing:
        return None, issues, "fact_brief.layers 缺少层：" + "、".join(missing)
    if not isinstance(sheet.get("questions"), list) or not sheet["questions"]:
        return None, issues, "缺少 questions"

    def keep_refs(where: str, refs: Any) -> list[str]:
        refs = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
        unknown = [r for r in refs if r not in known]
        if unknown:
            issues.append({"where": where, "code": "unknown_ref_removed", "refs": unknown})
        return [r for r in refs if r in known]

    def check_text(where: str, text: Any, refs: list[str]) -> dict[str, Any]:
        flags: dict[str, Any] = {}
        if not isinstance(text, str):
            return flags
        if FORBIDDEN.search(text):
            flags["forbidden_wording"] = FORBIDDEN.search(text).group(0)
            issues.append({"where": where, "code": "forbidden_wording", "match": flags["forbidden_wording"]})
        bad = _untraced_numbers(text, case_file, refs)
        if bad:
            flags["untraced_numbers"] = bad
            issues.append({"where": where, "code": "untraced_number_flagged", "numbers": bad})
        return flags

    out_layers = []
    for layer in layers:
        if not isinstance(layer, Mapping) or layer.get("layer") not in BRIEF_LAYERS:
            issues.append({"where": "fact_brief.layers", "code": "layer_row_invalid_dropped"})
            continue
        name = layer["layer"]
        status = layer.get("status") if layer.get("status") in LAYER_STATUS else "unknown"
        facts = []
        for i, fact in enumerate(layer.get("facts") or []):
            if not isinstance(fact, Mapping):
                continue
            where = f"fact_brief.{name}.facts[{i}]"
            refs = keep_refs(where, fact.get("refs"))
            if not refs:
                issues.append({"where": where, "code": "fact_without_valid_ref_dropped"})
                continue
            facts.append({"text_cn": fact.get("text_cn"), "refs": refs, **check_text(where, fact.get("text_cn"), refs)})
        out_layers.append({"layer": name, "status": status, "facts": facts,
                           "relations_cn": [r for r in layer.get("relations_cn") or [] if isinstance(r, str)],
                           "quality_cn": layer.get("quality_cn")})
    cross = []
    for i, row in enumerate((brief or {}).get("cross_layer") or []):
        if isinstance(row, Mapping) and row.get("kind") in CROSS_KINDS:
            refs = keep_refs(f"cross_layer[{i}]", row.get("refs"))
            if len(refs) >= 2:
                cross.append({"kind": row["kind"], "text_cn": row.get("text_cn"), "refs": refs,
                              **check_text(f"cross_layer[{i}]", row.get("text_cn"), refs)})
            else:
                issues.append({"where": f"cross_layer[{i}]", "code": "cross_layer_needs_two_refs_dropped"})
        else:
            issues.append({"where": f"cross_layer[{i}]", "code": "cross_layer_invalid_dropped"})
    anomalies = []
    for i, row in enumerate(sheet.get("anomalies") or []):
        if isinstance(row, Mapping) and row.get("kind") in ANOMALY_KINDS:
            refs = keep_refs(f"anomalies[{i}]", row.get("refs"))
            if refs:
                anomalies.append({"id": row.get("id"), "kind": row["kind"], "text_cn": row.get("text_cn"),
                                  "refs": refs, **check_text(f"anomalies[{i}]", row.get("text_cn"), refs)})
                continue
        issues.append({"where": f"anomalies[{i}]", "code": "anomaly_invalid_dropped"})
    questions = []
    for i, q in enumerate(sheet["questions"]):
        where = f"questions[{i}]"
        if not isinstance(q, Mapping):
            continue
        hyps = [h for h in q.get("hypotheses") or [] if isinstance(h, Mapping) and h.get("h_cn")]
        bears = [b for b in q.get("bears_on") or [] if b in BEARS_ON] if isinstance(q.get("bears_on"), list) else []
        if (q.get("type") not in QUESTION_TYPES or q.get("priority") not in PRIORITIES or not bears
                or q.get("answer_source") not in ANSWER_SOURCES or len(hyps) < 2):
            issues.append({"where": where, "code": "question_shape_invalid_dropped", "id": q.get("id")})
            continue
        premises = []
        for j, p in enumerate(q.get("premises") or []):
            if not isinstance(p, Mapping):
                continue
            refs = keep_refs(f"{where}.premises[{j}]", p.get("refs"))
            if refs:
                premises.append({"claim_cn": p.get("claim_cn"), "refs": refs,
                                 **check_text(f"{where}.premises[{j}]", p.get("claim_cn"), refs)})
        if not premises:
            issues.append({"where": where, "code": "question_without_grounded_premise_dropped", "id": q.get("id")})
            continue
        # free text (question, hypotheses, why) may cite any entry: a number there is flagged
        # only when it appears nowhere in the case; facts and premises are traced per ref
        whole = sorted(known)
        flags = check_text(where + ".question_cn", q.get("question_cn"), whole)
        for j, h in enumerate(hyps):
            for field in ("h_cn", "mechanism_cn", "would_support_cn", "would_refute_cn"):
                hflags = check_text(f"{where}.hypotheses[{j}].{field}", h.get(field), whole)
                if hflags.get("forbidden_wording"):
                    flags.setdefault("forbidden_wording", hflags["forbidden_wording"])
        check_text(where + ".why_material_cn", q.get("why_material_cn"), whole)
        questions.append({
            "id": q.get("id"), "type": q["type"], "bears_on": bears, "priority": q["priority"],
            "question_cn": q.get("question_cn"), "premises": premises, "why_material_cn": q.get("why_material_cn"),
            "hypotheses": [{k: h.get(k) for k in ("h_cn", "mechanism_cn", "would_support_cn", "would_refute_cn")}
                           | {"direction": h.get("direction") if h.get("direction") in DIRECTIONS else "unclear"}
                           for h in hyps],
            "answer_source": q["answer_source"],
            "from_anomalies": [a for a in q.get("from_anomalies") or [] if isinstance(a, str)
                               and a in {row["id"] for row in anomalies}], **flags})
    if not questions:
        return None, issues, "没有通过检查的问题（每题需合法类型/优先级/bears_on/答案来源、至少 2 个假设、至少 1 个有效引用的前提）"
    order = {"high": 0, "medium": 1, "low": 2}
    questions.sort(key=lambda q: order[q["priority"]])
    cleaned = {"protocol": PROTOCOL, "case_id": case_file.get("case_id"),
               "fact_brief": {"layers": out_layers, "cross_layer": cross},
               "anomalies": anomalies, "questions": questions,
               "coverage_note_cn": sheet.get("coverage_note_cn"),
               "types_covered": sorted({q["type"] for q in questions})}
    return cleaned, issues, None


def _content(response: Mapping[str, Any]) -> tuple[str, str | None]:
    choices = response.get("choices") or []
    if not choices:
        return "", None
    first = choices[0] or {}
    return ((first.get("message") or {}).get("content") or ""), first.get("finish_reason")


def _parse(text: str) -> Any:
    """JSON, tolerating a ```json fence or prose around one outer object."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _status(issues: list[dict[str, Any]]) -> str:
    degraded = any(i["code"].endswith("_dropped") or i["code"] == "forbidden_wording" for i in issues)
    return "PARTIAL" if degraded else "OK"


def run(case_file: Mapping[str, Any], *, api_key: str, model: str, effort: str = "high",
        transport: Callable[..., Mapping[str, Any]] | None = None, timeout: int = 600,
        endpoint: str | None = None) -> dict[str, Any]:
    assert_no_l9(case_file)
    post = transport or llm_core._post_chat_completion
    base = {"schema": SCHEMA, "prompt_version": PROMPT_VERSION, "requested_model": model, "effort": effort}
    attempts: list[dict[str, Any]] = []
    repair, previous = None, None
    for _ in range(2):
        request = build_request(case_file, model, effort=effort, repair_note=repair, previous=previous)
        try:
            response = post(api_key, model, request, timeout, endpoint)
        except Exception as exc:  # transport failure: keep what was tried
            attempts.append({"error": type(exc).__name__, "repair": bool(repair)})
            return {**base, "status": "REJECTED", "attempts": attempts, "sheet": None,
                    "issues": [{"code": "transport_error", "detail": type(exc).__name__}]}
        text, finish = _content(response)
        attempts.append({"response_model": response.get("model"), "usage": response.get("usage"),
                         "finish_reason": finish, "repair": bool(repair)})
        if finish == "length":
            # a truncated answer cannot be repaired in place: ask for a shorter one
            repair, previous = ("上次输出超出长度被截断。请精简后重新输出完整 JSON：每层事实不超过 6 条，"
                                "问题不超过 8 个，每题前提不超过 4 条。"), None
            continue
        try:
            parsed = _parse(text)
        except (TypeError, ValueError) as exc:
            repair, previous = "JSON 无法解析（" + type(exc).__name__ + "）", text
            continue
        sheet, issues, error = validate(parsed, case_file)
        if error is None:
            return {**base, "status": _status(issues), "attempts": attempts, "sheet": sheet, "issues": issues}
        repair, previous = error, text
    return {**base, "status": "REJECTED", "attempts": attempts, "sheet": None,
            "issues": [{"code": "structural", "detail": repair}]}


def to_markdown(sheet: Mapping[str, Any]) -> str:
    lines = [f"# 前置基础（事实底稿 + 问题单）　{sheet.get('case_id')}", "", "## 事实底稿", ""]
    for layer in sheet["fact_brief"]["layers"]:
        lines.append(f"### {layer['layer']}（{layer['status']}）")
        for fact in layer["facts"]:
            lines.append(f"- {fact['text_cn']}　`{'、'.join(fact['refs'])}`")
        for rel in layer.get("relations_cn") or []:
            lines.append(f"- 关系：{rel}")
        if layer.get("quality_cn"):
            lines.append(f"- 质量：{layer['quality_cn']}")
        lines.append("")
    if sheet["fact_brief"]["cross_layer"]:
        lines += ["### 跨层关系", ""] + [f"- [{c['kind']}] {c['text_cn']}　`{'、'.join(c['refs'])}`"
                                          for c in sheet["fact_brief"]["cross_layer"]] + [""]
    if sheet.get("anomalies"):
        lines += ["## 异常", ""] + [f"- {a['id']} [{a['kind']}] {a['text_cn']}　`{'、'.join(a['refs'])}`"
                                     for a in sheet["anomalies"]] + [""]
    lines += ["## 问题单", ""]
    for q in sheet["questions"]:
        lines += [f"### {q['id']}（{q['priority']}｜{q['type']}｜影响：{'、'.join(q['bears_on'])}）", "", q["question_cn"] or "", "",
                  "前提："] + [f"{i}. {p['claim_cn'] or ''}　`{'、'.join(p['refs'])}`" for i, p in enumerate(q["premises"], 1)]
        lines += ["", f"为何重要：{q.get('why_material_cn') or ''}", "", "竞争假设："]
        for h in q["hypotheses"]:
            lines.append(f"- {h['h_cn']}（成立则风险：{h['direction']}）机制：{h.get('mechanism_cn') or ''}；"
                         f"支持：{h.get('would_support_cn') or ''}；推翻：{h.get('would_refute_cn') or ''}")
        lines += ["", f"答案可能在：{q['answer_source']}", ""]
    return "\n".join(lines) + "\n"
