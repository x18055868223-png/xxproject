"""AI player, shadow track (docs/opus/35, docs/opus/28 step 5): one side of one round, read from the
frozen case, decided by a model under the rulebook, checked by the program, recorded. Never on
the page, never an order.

- System prompt: astra_player_rulebook.md, docs/opus/27 §4-§12 word for word (rulebook@1.0).
- Input: the player view, the inquirer's reading view of the case without the strategy-advice
  entries (docs/opus/28 C6), plus a 本轮 block from the case's selection (round, the round-1
  action in round 2, reservation price, friction, required edge, pass cost, max loss). Hard rules
  run before: the archive sends only cases whose selection says eligible.
- The play is to underwrite (rulebook 1.1): every side is accepted by default; a reject names the key
  adverse evidence. The player stops at the decision and its limit (the lock); no exits.
- Output: one player_decision@0.1. The program checks it and never asks for a rewrite (docs/opus/28
  step 5): enums, the action's required fields, refs that exist, no accept grounded on a reading
  rulebook §8 forbids, the limit price's required edge recomputed with the same formula
  (round_info.required_edge_at), round-1-only pass. Numbers not found in the cited entries, the
  本轮 block or the player's own limit fields are flagged, not rejected (as the inquirer).
- Result status: OK (valid), INVALID (a check failed), ERROR (no answer: transport, unparsable or
  truncated output). INVALID and ERROR are scored as invalid / missing, never as a reject.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from . import astra_case_file_v1 as case_file_core
    from . import astra_inquiry_v1 as inquiry
    from . import astra_inquiry_reading_v1 as reading
    from . import astra_round_info_v1 as round_info
    from . import signal_llm_review as llm_core
except ImportError:  # script-style import
    import astra_case_file_v1 as case_file_core
    import astra_inquiry_v1 as inquiry
    import astra_inquiry_reading_v1 as reading
    import astra_round_info_v1 as round_info
    import signal_llm_review as llm_core

PROMPT_VERSION = "player_prompt@0.2"   # 0.2: default to underwrite (rulebook 1.1)
RULEBOOK_VERSION = "rulebook@1.1"
SCHEMA = "player_decision@0.1"
RESULT_SCHEMA = "astra_player_result@0.1"
RULEBOOK_PATH = Path(__file__).with_name("astra_player_rulebook.md")
ROUND_BLOCK = "本轮"
HIDDEN_REFS = ("L8.guidance", "L8.signal_recommendation")      # strategy advice, old program readings (C6)
ACCEPT_FORBIDDEN = ("L3.", "L5.", "L2.baseline.")               # rulebook §8: never a reason to accept
ACTIONS = ("accept", "reject", "pass")
REJECT_REASONS = ("state_unfavourable", "pricing_unfavourable", "cannot_judge", "veto_or_missing")
DEVIATIONS = ("timing", "fill", "structure", "state")
GRADES = ("A", "B", "C", "X", "-")
MIN_VALID_MINUTES = 60
EDGE_TOLERANCE = 0.01             # required edge, as a share
LIMIT_GAP_TOLERANCE_BTC = 0.00002
TEXT_FIELDS = ("counter", "invalid_if", "below_reservation_why", "pass_cost", "next_round_needs", "edge_why")
# flagged, never invalid: they do not change what the player decided (untraced numbers, stray fields)
# forbidden_accept_basis: under the default to underwrite an accept needs no reason, so citing a §8 reading in
# its grounds describes the state; it is kept for attribution, never voids the decision (rulebook 1.1)
SOFT_CODES = {"untraced_number_flagged", "stray_fields_flagged", "edge_as_percent_flagged", "forbidden_accept_basis"}
DASHES = (None, "", "—", "–", "－", "-")
REF_SPLIT = re.compile(r"\s*[/、,，]\s*")   # one basis may cite several entries: "L2.quote / 本轮"

OUTPUT_SPEC = """【输出】只输出一个 JSON 对象，字段如下（键名照抄，值用中文；不要输出 JSON 之外的文字）：
{
 "action": "accept | reject | pass",
 "reject_reason": "拒绝时必填，四选一：state_unfavourable（状态不利） | pricing_unfavourable（定价不利） | cannot_judge（无法判断） | veto_or_missing（否决项或资料不足）；其他动作为 null",
 "deviation_types": ["接受时可选的接受理由类型：timing（时点） | fill（成交） | structure（结构） | state（状态）；不填为空列表；其他动作为空列表"],
 "limit_btc": "接受时必填：限价，毛信用，BTC（数字）；其他动作为 null",
 "valid_minutes": "接受时必填：限价有效期，分钟，不少于 60（整数）；其他动作为 null",
 "required_edge": "接受时必填：这个限价下的所需优势，按规则书 §7 第 5 条的公式算，用小数（0.18 表示 18%），限价不低于保留价时为 0；其他动作为 null",
 "limit_minus_reservation_btc": "接受时必填：限价 − 保留价，BTC（数字，可为负）；其他动作为 null",
 "edge_why": "接受时必填：一句话说明你为什么接受这个限价下的摩擦（所需优势）；其他动作为 null",
 "below_reservation_why": "接受且限价低于保留价时必填：为什么愿意低于市场估价成交；否则为 null",
 "pass_cost": "过牌时必填：等待的代价（用「本轮」给出的数）；否则为 null",
 "next_round_needs": "过牌时必填：第二轮要看到什么具体读数才接受；否则为 null",
 "basis": [{"ref": "引用的条目名（如 L2.quote、L6.session_clock）或 本轮；一条依据引用多个条目时用 / 分隔", "reading": "这些条目里的具体读数", "grade": "A | B | C | X | -（引用 §10 经验表时写其等级，案卷读数本身写 -）"}],
 "counter": "最强的一条相反证据",
 "invalid_if": "什么变化出现后这个决定不再成立（价位、时刻或读数）",
 "unknown": ["会影响决定、但案卷里没有或无法判断的信息"]
}
basis 一到三条。数字只用案卷与「本轮」里出现的值，以及你自己定的限价、有效期和由限价算出的所需优势。"""


def rulebook() -> str:
    return RULEBOOK_PATH.read_text(encoding="utf-8")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _r(value: Any, nd: int) -> Any:
    v = _num(value)
    return round(v, nd) if v is not None else None


def round_block(selection: Mapping[str, Any] | None, rnd: str, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The 本轮 block of rulebook §8, from the case's selection; program numbers only."""
    sel = selection or {}
    pricing = sel.get("pricing") or {}
    cost = ((sel.get("round") or {}).get("pass_cost") or {}) if rnd == "r1" else {}
    block: dict[str, Any] = {
        "轮次": "第一轮（02:00 UTC，到期前约 30 小时）" if rnd == "r1" else "第二轮（14:00 UTC，到期前约 18 小时；没有下一轮）",
        "保留价_btc": _r(pricing.get("reservation_btc"), 6), "两腿标记价差_btc": _r(pricing.get("mark_spread_btc"), 6),
        "费用_btc": _r(pricing.get("fee_btc"), 6),
        "按对手价毛信用_btc": _r(pricing.get("visible_credit_btc"), 6),
        "按中间价毛信用_btc": _r(pricing.get("mid_credit_btc"), 6),
        "摩擦门槛_按对手价_btc": _r(pricing.get("bid_gap_btc"), 6), "摩擦门槛_按中间价_btc": _r(pricing.get("mid_gap_btc"), 6),
        "所需优势_按对手价": _r(pricing.get("required_edge_bid"), 4), "所需优势_按中间价": _r(pricing.get("required_edge_mid"), 4),
        "所需优势说明": pricing.get("required_edge_note_cn"),
        "保留价可靠": pricing.get("reservation_reliable"),
        "最大损失_btc": _r(pricing.get("max_loss_btc"), 6), "最大损失_usd": _r(pricing.get("max_loss_usd"), 0),
        "提醒项": [c.get("title_cn") for c in sel.get("cautions") or [] if isinstance(c, Mapping)],
        "不知道的": list(sel.get("unknown_cn") or [])}
    if rnd == "r1":
        block["过到下一轮的代价"] = {"等到第二轮预计放弃的保费_btc": _r(cost.get("premium_btc"), 6),
                                "等到第二轮预计放弃的保费_usd": _r(cost.get("premium_usd"), 0),
                                "这段时间走掉的风险占比": _r(cost.get("variance_share"), 3),
                                "说明": cost.get("note_cn")}
    else:
        block["第一轮你的动作"] = _previous_cn(previous)
    return block


def _previous_cn(previous: Mapping[str, Any] | None) -> str:
    if not previous:
        return "第一轮没有你的动作记录（没有案卷、被硬规则否决或未运行）"
    action = {"reject": "拒绝", "pass": "过到下一轮", "accept": "接受"}.get(previous.get("action"), "无效或缺失")
    needs = previous.get("next_round_needs")
    text = f"第一轮你的动作：{action}；当时的合约 {previous.get('contract_key')}"
    return text + (f"；你写下的第二轮条件：{needs}" if needs else "")


def player_view(case: Mapping[str, Any], rnd: str, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The inquirer's reading view of the case without the strategy-advice entries, plus 本轮."""
    base = copy.deepcopy(case_file_core.for_inquirer(case))
    layers = {name: [e for e in entries if e.get("ref") not in HIDDEN_REFS]
              for name, entries in (base.get("layers") or {}).items()}
    base["layers"] = layers
    base["refs"] = {e["ref"]: {"layer": name, "index": i} for name, entries in layers.items()
                    for i, e in enumerate(entries) if e.get("ref")}
    seen = reading.view(base, chinese_keys=True)
    seen[ROUND_BLOCK] = round_block(case.get("selection"), rnd, previous)
    return seen


def build_request(view: Mapping[str, Any], model: str, *, effort: str = "high") -> dict[str, Any]:
    user = OUTPUT_SPEC + "\n\n事实案卷（L0–L8 与「本轮」）：\n" + json.dumps(view, ensure_ascii=False, separators=(",", ":"))
    return {"model": model, "messages": [{"role": "system", "content": rulebook()}, {"role": "user", "content": user}],
            "stream": False, "response_format": {"type": "json_object"}, "thinking": {"type": "enabled"},
            "reasoning_effort": effort, "max_tokens": 32768, "_local_prompt_version": PROMPT_VERSION}


def _traceable(view: Mapping[str, Any], own: Mapping[str, Any]) -> dict[str, Any]:
    """The view with 本轮 and the player's own limit numbers as citable entries, for number tracing."""
    out = dict(view)
    # the rulebook's own numbers (thresholds such as 0.25 expected moves) are not numbers outside the case
    out["layers"] = {**(view.get("layers") or {}),
                     ROUND_BLOCK: [{"ref": ROUND_BLOCK, "values": view.get(ROUND_BLOCK)}, {"ref": "_own", "values": own},
                                   {"ref": "_rulebook", "values": rulebook()}]}
    out["refs"] = {**(view.get("refs") or {}), ROUND_BLOCK: {"layer": ROUND_BLOCK, "index": 0},
                   "_own": {"layer": ROUND_BLOCK, "index": 1}, "_rulebook": {"layer": ROUND_BLOCK, "index": 2}}
    return out


def validate(decision: Any, view: Mapping[str, Any], rnd: str,
             pricing: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(the decision as given, issues). Any issue other than untraced_number_flagged makes it INVALID."""
    issues: list[dict[str, Any]] = []

    def bad(code: str, **detail: Any) -> None:
        issues.append({"code": code, **detail})

    if not isinstance(decision, Mapping):
        return None, [{"code": "not_an_object"}]
    d = dict(decision)
    action = d.get("action")
    if action not in ACTIONS:
        bad("action_invalid", value=action)
    if action == "pass" and rnd != "r1":
        bad("pass_outside_round_1")
    reason, devs = d.get("reject_reason"), d.get("deviation_types") or []
    if action == "reject" and reason not in REJECT_REASONS:
        bad("reject_reason_invalid", value=reason)
    stray = []
    if action != "reject" and reason not in (None, ""):
        stray.append("reject_reason")
    if not isinstance(devs, list) or any(t not in DEVIATIONS for t in devs):
        bad("deviation_type_invalid", value=devs)
    elif action != "accept" and devs:
        stray.append("deviation_types")
    limit, minutes = _num(d.get("limit_btc")), d.get("valid_minutes")
    if isinstance(minutes, float) and minutes.is_integer():
        minutes = int(minutes)
    edge, gap = _num(d.get("required_edge")), _num(d.get("limit_minus_reservation_btc"))
    if action == "accept" and edge is not None and edge > 1.0:
        edge = edge / 100.0          # given as a percent
        bad("edge_as_percent_flagged", value=d.get("required_edge"))
    pricing = pricing or {}
    if action == "accept":
        if limit is None or limit <= 0:
            bad("limit_missing")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < MIN_VALID_MINUTES:
            bad("valid_minutes_invalid", value=minutes)
        expected = round_info.required_edge_at(limit, pricing) if limit is not None else None
        if expected is None:
            if "fill" in devs:
                bad("fill_reason_without_reliable_edge")
        elif edge is None or abs(edge - expected) > EDGE_TOLERANCE:
            bad("required_edge_mismatch", given=edge, expected=round(expected, 4))
        reserve = _num(pricing.get("reservation_btc"))
        if reserve is not None and limit is not None:
            if gap is None or abs(gap - (limit - reserve)) > LIMIT_GAP_TOLERANCE_BTC:
                bad("limit_gap_mismatch", given=gap, expected=round(limit - reserve, 6))
            if limit < reserve and not str(d.get("below_reservation_why") or "").strip():
                bad("below_reservation_unexplained")
        if not str(d.get("edge_why") or "").strip():
            bad("accept_without_edge_why")
    else:
        stray += [f for f in ("limit_btc", "valid_minutes", "required_edge", "limit_minus_reservation_btc")
                  if d.get(f) is not None]
    if stray:
        bad("stray_fields_flagged", fields=stray)
    if action == "pass" and not (str(d.get("pass_cost") or "").strip() and str(d.get("next_round_needs") or "").strip()):
        bad("pass_without_cost_or_needs")
    basis = d.get("basis")
    refs = set((view.get("refs") or {})) | {ROUND_BLOCK}
    if not isinstance(basis, list) or not 1 <= len(basis) <= 3:
        bad("basis_count", value=len(basis) if isinstance(basis, list) else None)
        basis = basis if isinstance(basis, list) else []
    cited_refs: list[str] = []
    for b in basis:
        names = ([n.strip("「」") for n in REF_SPLIT.split(str(b.get("ref") or "").strip()) if n.strip("「」")]
                 if isinstance(b, Mapping) else [])
        if not names or any(n not in refs for n in names) or not str(b.get("reading") or "").strip():
            bad("basis_ref_invalid", value=(b or {}).get("ref") if isinstance(b, Mapping) else b)
            continue
        cited_refs += names
        grade = b.get("grade")
        # §10 writes some grades with a note ("B（边缘）", "方向 A，幅度 C"): its first grade letter counts
        letters = [c for c in str(grade or "") if c in "ABCX"]
        if grade not in DASHES and grade not in GRADES and not letters:
            bad("grade_invalid", value=b.get("grade"))
        if action == "accept" and any(n.startswith(ACCEPT_FORBIDDEN) for n in names):
            bad("forbidden_accept_basis", ref=b["ref"])
    for f in ("counter", "invalid_if"):
        if not str(d.get(f) or "").strip():
            bad("missing_" + f)
    if not isinstance(d.get("unknown"), list):
        bad("unknown_not_a_list")
    # numbers: traced against the cited entries, 本轮 and the player's own limit fields (flagged only)
    own = {k: d.get(k) for k in ("limit_btc", "valid_minutes", "required_edge", "limit_minus_reservation_btc")}
    traced = _traceable(view, own)
    cited = cited_refs + [ROUND_BLOCK, "_own", "_rulebook"]
    texts = [str(b.get("reading") or "") for b in basis if isinstance(b, Mapping)]
    loose = [str(d.get(f) or "") for f in TEXT_FIELDS] + [str(u) for u in d.get("unknown") or [] if isinstance(d.get("unknown"), list)]
    untraced = []
    for text in texts:
        untraced += inquiry._untraced_numbers(text, traced, cited)
    all_refs = list(traced["refs"])
    for text in loose:
        untraced += inquiry._untraced_numbers(text, traced, all_refs)
    if untraced:
        bad("untraced_number_flagged", numbers=untraced)
    return d, issues


def run(case: Mapping[str, Any], *, rnd: str, previous: Mapping[str, Any] | None = None, api_key: str,
        model: str, effort: str = "high", transport: Callable[..., Mapping[str, Any]] | None = None,
        timeout: int = 300, endpoint: str | None = None) -> dict[str, Any]:
    """One model call, no rewrite. Status OK / INVALID / ERROR."""
    view = player_view(case, rnd, previous)
    pricing = (case.get("selection") or {}).get("pricing") or {}
    base = {"schema": RESULT_SCHEMA, "decision_schema": SCHEMA, "prompt_version": PROMPT_VERSION,
            "rulebook": RULEBOOK_VERSION, "requested_model": model, "effort": effort, "round": rnd,
            "case_id": case.get("case_id"), "reading_view": reading.VERSION}
    post = transport or llm_core._post_chat_completion
    try:
        response = post(api_key, model, build_request(view, model, effort=effort), timeout, endpoint)
    except Exception as exc:
        return {**base, "status": "ERROR", "decision": None, "issues": [{"code": "transport_error",
                "detail": type(exc).__name__}], "attempt": None}
    text, finish = inquiry._content(response)
    message = ((response.get("choices") or [{}])[0] or {}).get("message") or {}
    attempt = {"response_model": response.get("model"), "usage": response.get("usage"), "finish_reason": finish,
               # the model's own reasoning, cut: kept for attribution, never checked
               "reasoning": str(message.get("reasoning_content") or "")[:6000] or None}
    if finish == "length":
        return {**base, "status": "ERROR", "decision": None, "issues": [{"code": "truncated"}], "attempt": attempt}
    try:
        parsed = inquiry._parse(text)
    except (TypeError, ValueError) as exc:
        return {**base, "status": "ERROR", "decision": None, "issues": [{"code": "unparsable",
                "detail": type(exc).__name__}], "attempt": attempt, "raw": text[:2000]}
    decision, issues = validate(parsed, view, rnd, pricing)
    hard = [i for i in issues if i["code"] not in SOFT_CODES]
    return {**base, "status": "INVALID" if hard else "OK", "decision": decision, "issues": issues, "attempt": attempt}


def metrics(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    """What the isolation test reports (docs/opus/35 §5)."""
    n = len(results)
    by_status: dict[str, int] = {}
    by_action: dict[str, int] = {}
    codes: dict[str, int] = {}
    untraced = 0
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["status"] == "OK":
            a = (r.get("decision") or {}).get("action")
            by_action[a] = by_action.get(a, 0) + 1
        for i in r.get("issues") or []:
            codes[i["code"]] = codes.get(i["code"], 0) + 1
            untraced += len(i.get("numbers") or []) if i["code"] == "untraced_number_flagged" else 0
    ok = by_status.get("OK", 0)
    return {"n": n, "by_status": by_status, "valid_share": ok / n if n else None, "by_action": by_action,
            "issue_codes": codes, "untraced_numbers": untraced,
            "untraced_per_decision": untraced / n if n else None,
            "all_same_action": len(by_action) == 1 and ok > 1}
