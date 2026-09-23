"""Local joint-opinion validation; numerical predictions remain immutable."""
from __future__ import annotations
import copy
import math

SCHEMA = "astra_joint_opinion@1.0.0"
RECOMMENDATIONS = ("put_credit", "call_credit", "watch", "insufficient")
VERDICTS = ("supports", "contradicts", "uncertain")
FIELDS = {"recommendation", "mechanism_verdict", "summary_cn", "evidence_roles",
          "applicability_gaps_cn", "strengthen_if_cn", "weaken_if_cn"}
ROLES = ("supports_applicability", "counters_applicability", "context_only")

def response_schema():
    text_list = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "additionalProperties": False, "required": sorted(FIELDS),
            "properties": {"recommendation": {"enum": list(RECOMMENDATIONS)},
                "mechanism_verdict": {"enum": list(VERDICTS)}, "summary_cn": {"type": "string"},
                "evidence_roles": {"type": "array", "items": {"type": "object",
                    "additionalProperties": False, "required": ["ref", "role", "claim_cn"],
                    "properties": {"ref": {"type": "string"}, "role": {"enum": list(ROLES)},
                                   "claim_cn": {"type": "string"}}}},
                **{k: text_list for k in ("applicability_gaps_cn", "strengthen_if_cn", "weaken_if_cn")}}}

INSTRUCTIONS_CN = (
    "\n联合复核职责：额外返回 joint_review 对象，只填 recommendation、mechanism_verdict、summary_cn、"
    "evidence_roles、applicability_gaps_cn、strengthen_if_cn、weaken_if_cn。"
    "recommendation=put_credit/call_credit/watch/insufficient；mechanism_verdict=supports/contradicts/uncertain。"
    "先辨认输入统计风险排序，再核对结构、压力和响应是否支持其适用性，最后说明维持、调整或暂缓建议。"
    "evidence_roles 每项为 ref、role、claim_cn；role=supports_applicability/counters_applicability/context_only；"
    "只引用卡时有效市场事实，不把模型估计列为市场事实，不重复填写统计数值或概率。"
    "墙距或结构距离只能说明位置，不能单独证明承接、强度或适用性；同源市场事实、"
    "同一统计摘要或同一模型意见不能当作独立确认。"
    "mechanism_verdict=supports需有supports_applicability证据，contradicts需有counters_applicability证据；"
    "只有背景或资料缺口时用uncertain，不把缺失说成市场已经反对。"
    "调整方向或暂缓必须引用实际反证，或具体说明适用性缺口；未来可能反转不是单独理由。"
    "没有统计摘要时 recommendation=insufficient、mechanism_verdict=uncertain，保持原市场分析。"
    "净Gamma负号本身不证明极端风险或必须回避，应结合来源、尺度、压力及价格响应。"
    "summary_cn 是本卡联合建议；advisory_guidance 简述同一建议的空间与补偿取舍，不另造相反结论。"
    "联合建议是研究建议，不更改D–S、统计预测或机器许可。"
)

def statistical_ranking(context):
    assessment = (context or {}).get("assessment") or {}
    sides = assessment.get("sides") or {}
    values = {}
    for name in ("put", "call"):
        side = sides.get(name) or {}
        value = side.get("expected_loss_normalized")
        if side.get("status") != "available" or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return "not_comparable"
        values[name] = value
    if math.isclose(values["put"], values["call"], rel_tol=1e-8, abs_tol=1e-12):
        return "tie"
    return min(values, key=values.get) + "_credit"

def normalize(raw, context, facts, asof):
    # Import lazily to reuse exactly the existing fact/time and human-text policy.
    import signal_review_v2 as core
    rank = statistical_ranking(context)
    result = {"schema": SCHEMA, "status": "UNAVAILABLE", "statistical_preference": rank,
              "recommendation": "insufficient", "mechanism_verdict": "uncertain",
              "summary_cn": "", "evidence_roles": [], "applicability_gaps_cn": [],
              "strengthen_if_cn": [], "weaken_if_cn": [], "changed_statistical_preference": False,
              "statistical_assessment_hash": ((context or {}).get("assessment") or {}).get("assessment_hash"),
              "validation_reasons_cn": [], "model_output": copy.deepcopy(raw)}
    reasons = result["validation_reasons_cn"]
    if not context or rank == "not_comparable":
        reasons.append("本卡统计估计未就绪或两侧不完整，联合确认暂不可用，原市场分析仍保留。")
        return result
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        reasons.append("联合意见结构不完整，未形成有效联合建议。")
        return result
    if raw["recommendation"] not in RECOMMENDATIONS or raw["mechanism_verdict"] not in VERDICTS:
        reasons.append("联合意见类型无效。")
    for key in ("applicability_gaps_cn", "strengthen_if_cn", "weaken_if_cn"):
        arr = raw[key]
        if not isinstance(arr, list) or not all(isinstance(x, str) and x.strip() and not core._v21_human_text_issue(x) for x in arr):
            reasons.append("联合意见的观察条件或缺口说明无效。")
        else:
            result[key] = list(arr)
    if not isinstance(raw["summary_cn"], str) or not raw["summary_cn"].strip() or core._v21_human_text_issue(raw["summary_cn"]):
        reasons.append("联合建议缺少有效中文解释。")
    roles = raw["evidence_roles"]
    if not isinstance(roles, list):
        reasons.append("联合意见证据列表无效。")
        roles = []
    for item in roles:
        if not isinstance(item, dict) or set(item) != {"ref", "role", "claim_cn"}:
            reasons.append("联合意见证据结构无效。")
            continue
        fact = facts.get(item["ref"]) if isinstance(item["ref"], str) else None
        if not fact or not core._fact_is_usable(fact, as_of_ms=asof)[0]:
            reasons.append("联合意见引用不存在、不可用或晚于卡片时点。")
            continue
        if item["role"] not in ROLES or not isinstance(item["claim_cn"], str) or not item["claim_cn"].strip() or core._v21_human_text_issue(item["claim_cn"]):
            reasons.append("联合意见证据作用说明无效。")
            continue
        assertion_issues = core._fact_assertion_issues(
            {"claim_cn": item["claim_cn"]},
            core._fact_assertion_context_for_ref(facts, item["ref"]),
        )
        if assertion_issues:
            reasons.extend("联合意见" + issue for issue in assertion_issues)
            continue
        result["evidence_roles"].append(copy.deepcopy(item))
    changed = raw["recommendation"] in ("put_credit", "call_credit", "watch") and raw["recommendation"] != rank
    counters = [x for x in result["evidence_roles"] if x["role"] == "counters_applicability"]
    supports = [x for x in result["evidence_roles"] if x["role"] == "supports_applicability"]
    if raw['mechanism_verdict']=='supports' and not supports:
        reasons.append('机制支持结论缺少对应支持证据，背景说明不能代替确认。')
    if raw['mechanism_verdict']=='contradicts' and not counters:
        reasons.append('机制反对结论缺少对应反证，资料缺失应表达为适用性待核对。')
    if changed and not (rank == "tie" and raw["recommendation"] in ("put_credit", "call_credit")) and not counters and not result["applicability_gaps_cn"]:
        reasons.append("调整或暂缓统计建议缺少实际反证或适用性缺口。")
    if raw["recommendation"] in ("put_credit", "call_credit") and not result["evidence_roles"]:
        reasons.append("联合选侧缺少可追溯市场依据。")
    if raw["recommendation"] == "insufficient":
        reasons.append("模型尚未形成有效联合判断。")
    if reasons:
        return result
    result.update(status="ASSESSED", recommendation=raw["recommendation"],
                  mechanism_verdict=raw["mechanism_verdict"], summary_cn=raw["summary_cn"].strip(),
                  changed_statistical_preference=changed)
    return result

def validate(value, context, facts, asof):
    if not isinstance(value, dict) or value != normalize(value.get("model_output"), context, facts, asof):
        raise ValueError("joint opinion does not match frozen evidence and statistical context")
    return value

def summary(value):
    return {k: copy.deepcopy(v) for k, v in (value or {}).items() if k not in {"model_output", "schema"}}
