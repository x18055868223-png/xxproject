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
    from . import astra_inquiry_reading_v1 as reading
    from . import signal_llm_review as llm_core
else:
    import astra_inquiry_reading_v1 as reading
    import signal_llm_review as llm_core

PROTOCOL = "inquiry_sheet@0.2"
PROMPT_VERSION = "inquiry_prompt@0.4"
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
NEGATION = re.compile(r"(不|无|没有|非|未)[^，。；、]{0,3}$")


def forbidden(text: str) -> str | None:
    """The first authorising or self-made-odds phrase, unless it is negated just before
    ("不能按期望值排序" restates the program's guidance; it is not a claim)."""
    for match in FORBIDDEN.finditer(text):
        if not NEGATION.search(text[max(0, match.start() - 5):match.start()]):
            return match.group(0)
    return None


# Machine codes in prose (docs/opus/18 §5): refs, snake_case names and enum codes, other English
# words outside the allowed abbreviations, millisecond clocks, unrounded decimals.
PROSE_ALLOWED = {w.lower() for w in (
    "IV", "RV", "ATM", "RR", "Delta", "Gamma", "Vega", "Theta", "OI", "GEX", "KPF", "TMV", "CVD", "EDB", "PCR",
    "ETF", "DVOL", "Put", "Call", "BTC", "USD", "USDT", "UTC", "Deribit", "Binance", "FMZ", "CME", "FOMC", "CPI",
    "PPI", "NFP", "Fed", "Flip", "Pin", "Max", "Pain", "MA", "DTE", "VIX", "SPX", "NYSE", "ETH", "Bitview",
    "Farside", "Coinbase", "CoinGlass", "MSTR", "IBIT", "Agent", "LLM", "high", "medium", "low",
    # market and project terms the canary sheets used as words, not as codes (2026-10-22)
    "QQQ", "NDX", "VXN", "Beta", "Skew", "Forward", "Vanna", "Charm", "Space", "Mark", "Wall", "GGR",
    "BTCUSDT", "Decision", "Path", "GDP", "PCE", "CPI", "Powell", "FOMC")}
PROSE_CODES = re.compile(
    r"(?<![A-Za-z0-9_.])L[0-9](?:\.[A-Za-z0-9_]+)+"                       # refs
    r"|(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_\-])"   # snake / ENUM_CODE
    r"|(?<![A-Za-z0-9_.\-])[A-Za-z]{3,}(?![A-Za-z0-9_\-])"                 # English words
    r"|(?<!\d)1[5-9]\d{11}(?!\d)"                                         # millisecond clocks
    r"|\d+(?:\.\d+)?\s*M\s*ms"                                           # "23.4M ms"
    r"|\d+\.\d{7,}")                                                      # unrounded decimals
CODE_REPAIR_THRESHOLD = 12
DUPLICATE_OVERLAP = 0.6


def code_tokens(text: Any) -> list[str]:
    """Machine codes a reader would see in this text (allowed abbreviations and contract names excluded)."""
    if not isinstance(text, str):
        return []
    return [m.group(0) for m in PROSE_CODES.finditer(text)
            if not (m.group(0).isalpha() and m.group(0).lower() in PROSE_ALLOWED)]


SYSTEM_PROMPT = """你是 xxproject 的证据提问者。研究对象是一张具体的 Deribit BTC 币本位垂直信用价差（1 BTC，持有到期，约 24 小时内到期）。
你的产出交给一位独立的外部研究员（自主 Agent，能联网、能计算、会逐条核查你的前提）。你的产出只有两件事：
一、把事实基础讲清楚（事实底稿）；二、有依据地提出少量高价值问题（问题单）。问题贵精不贵多。

你不做：不下结论、不判断该不该做、不授权、不计算新数字、不给任何概率或期望值。概率基线已由程序算好（L2.baseline），你只引用。

【文字规则（所有 *_cn 文本）】
1. 只用中文说法写给人读：条目用其 label_cn 的说法（如“本期墙”“责任区与距离”“日内时段”），字段用中文含义。
2. 正文里不写 ref（如 L1.liability）、不写英文字段名（带下划线的键名）、不写枚举码（如 NO_MOVE、BULLISH、net_credit_unknown）、不写毫秒数。ref 只写进 refs 字段。
3. 时间照抄案卷已给的 UTC 时刻文字（如 2026-10-22 13:30 UTC），时长写小时或分钟；数字照抄案卷的显示值，不要写更多位数。
4. 可以保留：合约名（如 BTC-23OCT26-83000-C）和通用缩写 IV、RV、ATM、RR、Delta、Gamma、OI、GEX、KPF、TMV、CVD、EDB、M-DIE、PCR、ETF、DVOL、Put、Call、BTC、USD、UTC。

【事实底稿规则】
1. 按 L0 到 L8 逐层写，每层都要写：status 取 material（有实质信息）/quiet（无实质信息）/unknown（缺数据或质量不可用）。
2. 每条事实必须带 refs。数字取自案卷显示值；百分比可由小数乘 100 表示；不得换算成案卷里没有的新量。
3. quality 为 stale/missing/unknown 的条目，不得当作已知事实陈述，只能写“缺失/过期”，并在该层 quality_cn 里说一次即可。
   现价以 L0.cross_section（价格截面）的锚为准；截面里不是当前的来源，其价格以及由它算出的距离、方向、Gamma 位置读数都不代表现在，也不再就“它们是否仍然有效”出题。
   quality_reason 为 walls_not_contract_expiry 或 iv_not_contract_expiry 的条目属于市场口径或另一个到期日（见该条 relations.expiry_alignment）：引用时写明口径，不得当作本合约到期日的事实。本合约到期日的结构与波动以 L3.expiry_walls、L4.expiry_vol 为准。
4. 只陈述，不推断。推断一律放进问题单的假设里。
5. 写出跨层关系 cross_layer：哪些层互相印证（corroborate）、互相矛盾（conflict）、或其实同源（collinear，看 lineage）。

【不出题的情形（去噪）】
1. 已声明的缺口：L0.gaps 中的缺口码、quality 不是 ok 的条目、价格截面里过期的来源、费用未知、风险参照缺失、数据源过期——这些在事实底稿里说明，不作为问题。
2. 口径不同不是冲突：relations.expiry_alignment 已说明属于另一到期日或市场口径的墙、IV、RR，不与本合约口径比对错，以本合约口径为准；只有本合约口径自身不确定时才提问。0DTE 墙的价位不属于本合约到期结构，但 0DTE 在本合约持有期内结算、对价格路径的影响，可以作为情景（scenario）提问。
3. 已有研究定论（17 号经验体系），不再作为问题：
   - 前一天的涨跌方向不能外推到次日（A 级）；趋势与成交类方向读数（TMV、EDB、主动成交）多为同源（C 级，可就同源与否提一题）；
   - 一天期 IV 分位高低不作危险或安全信号（X 级）；30 天指数（DVOL）低于近期实现或较昨日上升不作危险信号（X 级），其处于高位只是减弱的信号（C 级），不单独出题；
   - “空间位置相对有利”不作缓冲（X 级）；
   - 已公开的事件已经计入一天期报价（B 级）；
   - 风险在一天内按时段分布（A 级，见 L6.session_clock）：泛泛地问“美股时段风险是否更高”不算问题；只有当本合约的具体价位与某个时段的结构或事件相连时才提问。
4. 噪声题型（10-21 评定与 18 号设计稿）：补偿档位之争、同一到期日两个 IV 口径之争、数据源或版本之争、几分钟内的 Delta 或概率小幅变化、模型方法本身（如触及概率≈2×实值的近似）。
5. 一个根因最多一个问题；异常列表 anomalies 里的条目不要在问题里换个说法再问一次。
6. 不为凑题型、凑数量出题。

【高价值问题的判定（每题必须全部满足）】
- 有据：每个前提写成“陈述 + refs”，数字与案卷一致。
- 改变决定：why_material_cn 必须写明，答案落在不同假设时会改变哪个决定（做或不做、做哪一侧、短腿距离或宽度、持有中是否提前处理），以及它通过触界概率、到期亏损概率、深尾损失或补偿（bears_on）中的哪一项起作用。改变不了任何决定的问题不要提。
- 可判别：至少两个相互竞争的假设，每个写明机制、什么证据会支持、什么证据会推翻，以及它成立时风险升高还是降低。
- 可回答：写明答案可能在哪找（案卷内 / 历史 / 外部联网 / 无法获得）。整张问题单中至少一半问题的 answer_source 为 case_file 或 history；answer_source 为 external 的，在 question_cn 末尾用括号写明需要哪一项外部数据。
- 可检验：每题至少有一个假设，其 would_support_cn 或 would_refute_cn 写成到期前能在 BTC 指数路径或交割价上看到的现象（如到期前是否触及短腿、交割价落在盈亏平衡与保护腿之间还是之外、到期前是否越过某个本期墙位），使问题在到期后能用存档的指数路径与交割价检验。价位只用案卷已有的价位（短腿、保护腿、盈亏平衡、墙位、现价锚），不自设新价位。
- 非冗余：不是事实底稿或 L8 程序读数的复述。L8 是可以被质疑的程序判读，与 L8 结论相同的问题不算关键问题；与 L8 冲突的证据优先提问。
  L8.research_readings 是经前向验证支持、并经用户批准的研究读数：写明它是否适用于本合约；当本合约的其他证据与它的方向冲突时，优先就此提问。

【数量与排序】问题 3 到 6 个，其中 high 不超过 3 个。证据不足以形成高价值问题时宁可少提，并在 coverage_note_cn 说明。priority 按“答案能在多大程度上让风险偏离市场基线”排序。

【问题类型】conflict（证据相反）、collinear（多个信号是否同一驱动，每张单最多一题）、temporal（持续还是噪声）、gap（缺失数据会否改变判断，只限未被声明、且能改变决定的缺口）、mechanism（框架外的机制或关联）、compensation（补偿与风险是否错配）、scenario（到期前什么路径会触发责任）。类型尽量多样，但不为凑类型出题。

【轻幻觉规则】
- 前提里的数字必须来自所引条目的显示值。
- 外扩推断只能写成假设；通用市场机制（如做市商对冲）可以用，但要写明它是机制假设，不是已发生的事实。
- 不引入案卷外的数字。证据不足就写 unknown，不要猜。
- 币本位：赔付以 BTC 计。Put 跌破保护腿后 BTC 赔付随价格下降继续增加；Call 越过保护腿后 BTC 赔付按 宽度/价格 递减；USD 赔付封顶于宽度。赔付表中保护腿一半或两倍处的点只示意形状，一天内不可达，不作为深尾情景。USD 与 BTC 不混用。
- Q1（风险质量）与 Q2（风险补偿）不能互相替代；外部叙事不能覆盖合同事实。

【好问题与伪问题示例】（refs 只写在 refs 字段，正文不写）
好：question_cn“到期前若下探，保护腿 79500 一带的本期 Put Gamma 集中会让下跌减速钉住，还是被穿越后加速？”，前提“现价距短腿 0.50%”（refs: L1.liability）、“本期 Put 方 Gamma 最大集中在 79500”（refs: L3.expiry_walls）、“15 分钟运动判读为无方向”（refs: L5.m_die）；why_material_cn“钉住则深尾损失低、可维持当前短腿距离；穿越则深尾损失高，应选更远的短腿或不做”。
好：question_cn“可见信用比两腿 mark 价差低，是我们的成交口径吃亏，还是市场没给这一侧风险加价？”；why_material_cn“前者只影响补偿、改为挂单接近中间价即可；后者意味着风险可能被低估，应不做这一侧”。
伪：“短腿距现价 0.50%，是否较近？”（复述事实，无竞争假设）
伪：“现价到底是哪一个？”（价格截面已给出现价锚与各来源年龄）
伪：“费用未知，会不会改变判断？”（已声明的缺口）
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
fact_brief.layers 必须恰好覆盖 L0–L8 九层。问题 3–6 个，每个问题至少 2 个 hypotheses。按 priority 从高到低排列。"""


def assert_no_l9(case_file: Mapping[str, Any]) -> None:
    """The inquirer reads the current cross-section only; history (L9) never reaches it."""
    if "L9" in (case_file.get("layers") or {}) or any(
            str(r).startswith("L9.") for r in (case_file.get("refs") or {})):
        raise ValueError("the inquirer must not receive L9; use astra_case_file_v1.for_inquirer")


def build_request(case_file: Mapping[str, Any], model: str, *, effort: str = "high",
                  repair_note: str | None = None, previous: str | None = None,
                  rewrite: bool = False) -> dict[str, Any]:
    """`rewrite`: the note asks for a wording rewrite of a valid sheet, not a format repair."""
    assert_no_l9(case_file)
    case_json = json.dumps(case_file, ensure_ascii=False, separators=(",", ":"))
    user = OUTPUT_SPEC + "\n\n事实案卷（L0–L8，按层排列）：\n" + case_json
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    if repair_note:
        messages += [{"role": "assistant", "content": previous or "（上次输出为空）"},
                     {"role": "user", "content": repair_note if rewrite else "上面的输出未通过格式检查：" + repair_note +
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
            walk(entry.get("asof"))          # the reading view's entry time, e.g. "2026-10-22 00:27 UTC"
            walk(entry.get("label_cn"))      # "近 72 小时 Space 门 / 锚定迁移"
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
        hit = forbidden(text)
        if hit:
            flags["forbidden_wording"] = hit
            issues.append({"where": where, "code": "forbidden_wording", "match": hit})
        bad = _untraced_numbers(text, case_file, refs)
        if bad:
            flags["untraced_numbers"] = bad
            issues.append({"where": where, "code": "untraced_number_flagged", "numbers": bad})
        codes = code_tokens(text)
        if codes:
            flags["code_tokens"] = codes
            issues.append({"where": where, "code": "code_tokens_flagged", "tokens": codes})
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
    answerable = sum(1 for q in questions if q["answer_source"] in {"case_file", "history"})
    if answerable * 2 < len(questions):
        issues.append({"where": "questions", "code": "answer_source_mostly_external",
                       "answerable": answerable, "total": len(questions)})
    for layer in out_layers:
        check_text(f"fact_brief.{layer['layer']}.quality_cn", layer.get("quality_cn"),
                   [r for r in known if r.split(".", 1)[0] == layer["layer"]] + ["L0.cross_section"] * ("L0.cross_section" in known))
    check_text("coverage_note_cn", sheet.get("coverage_note_cn"), sorted(known))
    order = {"high": 0, "medium": 1, "low": 2}
    questions.sort(key=lambda q: order[q["priority"]])
    for j, q in enumerate(questions):
        mine = {r for p in q["premises"] for r in p["refs"]}
        for earlier in questions[:j]:
            theirs = {r for p in earlier["premises"] for r in p["refs"]}
            if (earlier["type"] == q["type"] and mine and theirs
                    and len(mine & theirs) / len(mine | theirs) >= DUPLICATE_OVERLAP):
                q["possible_duplicate_of"] = earlier.get("id") or f"#{questions.index(earlier) + 1}"
                issues.append({"where": f"questions.{q.get('id')}", "code": "possible_duplicate_flagged",
                               "of": earlier.get("id")})
                break
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


def metrics(sheet: Mapping[str, Any], issues: list[dict[str, Any]]) -> dict[str, int]:
    """Counts the canary and the archive compare across prompt versions (docs/opus/18 §7)."""
    return {"questions": len(sheet.get("questions") or []),
            "high": sum(1 for q in sheet.get("questions") or [] if q.get("priority") == "high"),
            "anomalies": len(sheet.get("anomalies") or []),
            "code_tokens": sum(len(i["tokens"]) for i in issues if i["code"] == "code_tokens_flagged"),
            "untraced_numbers": sum(len(i["numbers"]) for i in issues if i["code"] == "untraced_number_flagged"),
            "possible_duplicates": sum(1 for i in issues if i["code"] == "possible_duplicate_flagged")}


def _code_note(issues: list[dict[str, Any]]) -> str:
    tokens = list(dict.fromkeys(t for i in issues if i["code"] == "code_tokens_flagged" for t in i["tokens"]))
    return ("正文里仍有机器码：" + "、".join(tokens[:40]) + ("等" if len(tokens) > 40 else "") +
            "。请按【文字规则】把这些改成中文说法（ref 只留在 refs 字段，时间用 UTC 时刻文字，数字用案卷显示值），"
            "其余内容不变，重新输出完整的 JSON 对象。")


def _content_kept(first: tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]],
                  second: tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]) -> bool:
    """A code rewrite may change wording only: it keeps every question and fact and drops no
    more items in validation than the first sheet did."""
    def facts(sheet: Mapping[str, Any]) -> int:
        return sum(len(layer["facts"]) for layer in sheet["fact_brief"]["layers"])

    def dropped(issues: list[dict[str, Any]]) -> int:
        return sum(1 for i in issues if i["code"].endswith("_dropped"))
    return (second[2]["questions"] >= first[2]["questions"] and facts(second[0]) >= facts(first[0])
            and dropped(second[1]) <= dropped(first[1]))


def run(case_file: Mapping[str, Any], *, api_key: str, model: str, effort: str = "high",
        transport: Callable[..., Mapping[str, Any]] | None = None, timeout: int = 600,
        endpoint: str | None = None, chinese_keys: bool = True) -> dict[str, Any]:
    """At most one structural repair (unparsable, truncated, invalid shape) and, once a sheet
    validates with more than CODE_REPAIR_THRESHOLD machine codes, at most one rewrite, adopted
    only when it validates with fewer codes and keeps all questions and facts.
    Field names are read in Chinese by default: in the 10-22 canary that arm left 2-8 codes in
    one call, the other needed a rewrite (docs/opus/18 §7)."""
    assert_no_l9(case_file)
    seen = reading.view(case_file, chinese_keys=chinese_keys)
    post = transport or llm_core._post_chat_completion
    base = {"schema": SCHEMA, "prompt_version": PROMPT_VERSION, "requested_model": model, "effort": effort,
            "reading_view": reading.VERSION, "chinese_keys": chinese_keys}
    attempts: list[dict[str, Any]] = []
    repair: str | None = None
    previous: str | None = None
    kind: str | None = None          # what the next call repairs: None, "structural" or "code"
    structural_left = 1
    best: tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]] | None = None
    code_repair: dict[str, Any] = {}
    while True:
        request = build_request(seen, model, effort=effort, repair_note=repair, previous=previous,
                                rewrite=kind == "code")
        try:
            response = post(api_key, model, request, timeout, endpoint)
        except Exception as exc:  # transport failure: keep what was tried
            attempts.append({"error": type(exc).__name__, "repair": kind or False})
            if best is not None:
                code_repair["rewrite_error"] = "transport_error"
                break
            return {**base, "status": "REJECTED", "attempts": attempts, "sheet": None,
                    "issues": [{"code": "transport_error", "detail": type(exc).__name__}]}
        text, finish = _content(response)
        attempts.append({"response_model": response.get("model"), "usage": response.get("usage"),
                         "finish_reason": finish, "repair": kind or False})
        sheet, issues, error, text_for_repair = None, [], None, text
        if finish == "length":
            # a truncated answer cannot be repaired in place: ask for a shorter one
            error = ("上次输出超出长度被截断。请精简后重新输出完整 JSON：每层事实不超过 6 条，"
                     "问题不超过 6 个，每题前提不超过 4 条。")
            text_for_repair = None
        else:
            try:
                parsed = _parse(text)
            except (TypeError, ValueError) as exc:
                error = "JSON 无法解析（" + type(exc).__name__ + "）"
            else:
                sheet, issues, error = validate(parsed, seen)
        if kind == "code":
            # the rewrite only replaces the first sheet when it is strictly better
            if error is None and sheet is not None and best is not None:
                candidate = (sheet, issues, metrics(sheet, issues))
                code_repair["rewrite_code_tokens"] = candidate[2]["code_tokens"]
                code_repair["rewrite_questions"] = candidate[2]["questions"]
                if candidate[2]["code_tokens"] < best[2]["code_tokens"] and _content_kept(best, candidate):
                    best = candidate
                    code_repair["adopted"] = True
            else:
                code_repair["rewrite_error"] = error
            break
        if error is None and sheet is not None:
            best = (sheet, issues, metrics(sheet, issues))
            if best[2]["code_tokens"] > CODE_REPAIR_THRESHOLD:
                code_repair = {"tried": True, "adopted": False, "before": best[2]["code_tokens"]}
                kind, repair, previous = "code", _code_note(issues), text
                continue
            break
        if not structural_left:
            return {**base, "status": "REJECTED", "attempts": attempts, "sheet": None,
                    "issues": [{"code": "structural", "detail": error}]}
        structural_left -= 1
        kind, repair, previous = "structural", error, text_for_repair
    sheet, issues, m = best
    if code_repair:
        code_repair["after"] = m["code_tokens"]
    return {**base, "status": _status(issues), "attempts": attempts, "sheet": sheet, "issues": issues,
            "metrics": m, **({"code_repair": code_repair} if code_repair else {})}


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
