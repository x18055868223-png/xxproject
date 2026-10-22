import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_inquiry_v1 as inquiry

LAYERS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8")


def _case():
    entries = {
        "L1": [{"ref": "L1.liability", "values": {"signed_distance_to_short_pct": -0.5013, "starts_at_usd": 81500.0}}],
        "L2": [{"ref": "L2.baseline.p_itm_short", "values": {"value": 0.3099}}],
        "L3": [{"ref": "L3.expiry_walls", "values": {"top": {"gamma_put": [{"strike": 79500.0}]}}}],
        "L5": [{"ref": "L5.m_die", "values": {"state": "NO_MOVE"}}],
    }
    layers = {L: entries.get(L, []) for L in LAYERS}
    refs = {e["ref"]: {"layer": L, "index": i} for L, es in layers.items() for i, e in enumerate(es)}
    return {"case_id": "cf_x", "layers": layers, "refs": refs}


def _sheet(**over):
    sheet = {
        "protocol": "inquiry_sheet@0.2", "case_id": "cf_x",
        "fact_brief": {"layers": [{"layer": L, "status": "quiet", "facts": []} for L in LAYERS],
                       "cross_layer": [{"kind": "conflict", "text_cn": "墙在保护腿而运动为无", "refs": ["L3.expiry_walls", "L5.m_die"]}]},
        "anomalies": [{"id": "A1", "kind": "conflict", "text_cn": "x", "refs": ["L5.m_die"]}],
        "questions": [{
            "id": "K1", "type": "scenario", "bears_on": ["tail_loss"], "priority": "high",
            "question_cn": "79500 一带会钉住还是被穿越？",
            "premises": [{"claim_cn": "现价距短腿 0.50%", "refs": ["L1.liability"]},
                         {"claim_cn": "Put Gamma 集中在 79500", "refs": ["L3.expiry_walls", "L9.nope"]}],
            "why_material_cn": "决定深尾",
            "hypotheses": [{"h_cn": "钉住", "direction": "lowers"}, {"h_cn": "穿越", "direction": "raises"}],
            "answer_source": "external", "from_anomalies": ["A1"]}],
    }
    sheet["fact_brief"]["layers"][1]["facts"] = [{"text_cn": "短腿起赔 81500 USD", "refs": ["L1.liability"]}]
    sheet.update(over)
    return sheet


def test_valid_sheet_passes_and_unknown_refs_are_removed():
    sheet, issues, error = inquiry.validate(_sheet(), _case())
    assert error is None and sheet["questions"][0]["id"] == "K1"
    assert sheet["questions"][0]["premises"][1]["refs"] == ["L3.expiry_walls"]
    assert any(i["code"] == "unknown_ref_removed" and i["refs"] == ["L9.nope"] for i in issues)
    assert "untraced_numbers" not in sheet["questions"][0]["premises"][0]   # 0.50 traces to -0.5013


def test_untraced_number_is_flagged_not_dropped():
    s = _sheet()
    s["questions"][0]["premises"][0]["claim_cn"] = "现价距短腿 0.73%"
    sheet, issues, error = inquiry.validate(s, _case())
    assert error is None
    assert sheet["questions"][0]["premises"][0]["untraced_numbers"] == ["0.73"]


def test_baseline_percent_citation_traces():
    s = _sheet()
    s["fact_brief"]["layers"][2]["facts"] = [{"text_cn": "短腿到期实值概率 31.0%", "refs": ["L2.baseline.p_itm_short"]}]
    sheet, _, _ = inquiry.validate(s, _case())
    assert "untraced_numbers" not in sheet["fact_brief"]["layers"][2]["facts"][0]


def test_single_hypothesis_question_is_dropped_and_empty_sheet_needs_repair():
    s = _sheet()
    s["questions"][0]["hypotheses"] = s["questions"][0]["hypotheses"][:1]
    sheet, issues, error = inquiry.validate(s, _case())
    assert sheet is None and error and any(i["code"] == "question_shape_invalid_dropped" for i in issues)


def test_missing_layer_is_structural():
    s = _sheet()
    s["fact_brief"]["layers"] = s["fact_brief"]["layers"][:-1]
    assert inquiry.validate(s, _case())[2].endswith("L8")


def test_forbidden_wording_is_flagged():
    s = _sheet()
    s["questions"][0]["question_cn"] = "胜率高，建议承保吗？"
    sheet, issues, _ = inquiry.validate(s, _case())
    assert sheet["questions"][0]["forbidden_wording"] in {"胜率", "建议承保"}


def test_l9_must_never_reach_the_inquirer():
    case = _case()
    case["refs"]["L9.history"] = {"layer": "L9", "index": 0}
    with pytest.raises(ValueError):
        inquiry.validate(_sheet(), case)


def test_run_repairs_once_then_succeeds():
    calls = []
    bad = _sheet()
    bad["fact_brief"]["layers"] = bad["fact_brief"]["layers"][:3]
    replies = [bad, _sheet()]

    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        return {"model": "deepseek-flash", "choices": [{"message": {"content": json.dumps(replies[len(calls) - 1], ensure_ascii=False)}}]}

    result = inquiry.run(_case(), api_key="k", model="deepseek-flash", transport=transport)
    assert result["status"] == "OK" and len(calls) == 2
    assert "未通过格式检查" in calls[1]["messages"][-1]["content"]
    assert calls[0]["reasoning_effort"] == "high" and calls[0]["thinking"] == {"type": "enabled"}
    md = inquiry.to_markdown(result["sheet"])
    assert "## 问题单" in md and "K1" in md


def test_run_rejects_after_two_unparsable_replies():
    def transport(key, model, request, timeout, endpoint=None):
        return {"model": "deepseek-flash", "choices": [{"message": {"content": "not json"}}]}
    result = inquiry.run(_case(), api_key="k", model="deepseek-flash", transport=transport)
    assert result["status"] == "REJECTED" and result["sheet"] is None and len(result["attempts"]) == 2


def test_scientific_and_float_noise_numbers_trace():
    case = _case()
    case["layers"]["L5"].append({"ref": "L5.funding", "values": {"last_rate": 9.2e-05, "pct": 6.5544728}})
    case["refs"]["L5.funding"] = {"layer": "L5", "index": 1}
    s = _sheet()
    s["fact_brief"]["layers"][5]["facts"] = [{"text_cn": "资金费率 9.2e-05，占比 6.554472800000001", "refs": ["L5.funding"]}]
    sheet, _, _ = inquiry.validate(s, case)
    assert "untraced_numbers" not in sheet["fact_brief"]["layers"][5]["facts"][0]


def test_invented_integer_percent_is_not_traced_by_a_fraction():
    case = _case()   # L2.baseline.p_itm_short is 0.3099
    s = _sheet()
    s["fact_brief"]["layers"][2]["facts"] = [{"text_cn": "触及概率约 62%", "refs": ["L2.baseline.p_itm_short"]}]
    sheet, _, _ = inquiry.validate(s, case)
    assert sheet["fact_brief"]["layers"][2]["facts"][0]["untraced_numbers"] == ["62"]


def test_display_rounding_and_exponent_trace():
    s = _sheet()
    s["fact_brief"]["layers"][1]["facts"] = [{"text_cn": "起赔约 81,500 与 8.15e4 USD", "refs": ["L1.liability"]}]
    sheet, _, _ = inquiry.validate(s, _case())
    assert "untraced_numbers" not in sheet["fact_brief"]["layers"][1]["facts"][0]


def test_l9_layer_is_refused_before_any_request():
    case = _case()
    case["layers"]["L9"] = [{"ref": "L9.history", "values": {}}]
    with pytest.raises(ValueError):
        inquiry.run(case, api_key="k", model="m", transport=lambda *a, **k: pytest.fail("must not call"))


def test_forbidden_wording_makes_the_sheet_partial():
    s = _sheet()
    s["questions"][0]["hypotheses"][0]["mechanism_cn"] = "胜率高"
    def transport(key, model, request, timeout, endpoint=None):
        return {"choices": [{"message": {"content": "```json\n" + json.dumps(s, ensure_ascii=False) + "\n```"},
                             "finish_reason": "stop"}]}
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["status"] == "PARTIAL" and result["sheet"]["questions"][0]["forbidden_wording"] == "胜率"


def test_truncated_reply_asks_for_a_shorter_answer_without_echoing_it():
    calls = []
    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        if len(calls) == 1:
            return {"choices": [{"message": {"content": "{\"protocol\": \"inq"}, "finish_reason": "length"}]}
        return {"choices": [{"message": {"content": json.dumps(_sheet(), ensure_ascii=False)}, "finish_reason": "stop"}]}
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["status"] in {"OK", "PARTIAL"} and result["attempts"][0]["finish_reason"] == "length"
    assert "截断" in calls[1]["messages"][-1]["content"]
    assert calls[1]["messages"][-2]["content"] == "（上次输出为空）"


def test_transport_error_is_rejected_with_attempts():
    def transport(*a, **k):
        raise TimeoutError("slow")
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["status"] == "REJECTED" and result["attempts"] == [{"error": "TimeoutError", "repair": False}]


def test_free_text_traces_against_the_whole_case_but_flags_invented_numbers():
    s = _sheet()
    s["questions"][0]["question_cn"] = "实值 0.3099 时 79500 会钉住吗？"    # from refs outside the premises
    s["questions"][0]["hypotheses"][1]["mechanism_cn"] = "跌到 39,750 时赔付 0.048989 BTC"   # computed by the model
    sheet, issues, _ = inquiry.validate(s, _case())
    assert "untraced_numbers" not in sheet["questions"][0]
    assert any(i["where"].endswith("hypotheses[1].mechanism_cn") and "0.048989" in i["numbers"] for i in issues)


def test_prompt_0_4_keeps_expiry_rules_and_adds_wording_and_noise_rules():
    assert inquiry.PROMPT_VERSION == "inquiry_prompt@0.4"
    for phrase in ("walls_not_contract_expiry", "iv_not_contract_expiry", "至少一半问题", "可检验", "交割价",
                   "【文字规则", "ref 只写进 refs 字段", "【不出题的情形", "L0.cross_section", "改变不了任何决定",
                   "问题 3 到 6 个", "Call 越过保护腿后 BTC 赔付按 宽度/价格 递减"):
        assert phrase in inquiry.SYSTEM_PROMPT
    # the examples no longer teach refs inside the prose
    example = inquiry.SYSTEM_PROMPT.split("【好问题与伪问题示例】")[1]
    assert "（L1.liability）" not in example and "（L3.expiry_walls）" not in example


def test_mostly_external_questions_are_flagged_but_kept_and_status_stays_ok():
    sheet, issues, error = inquiry.validate(_sheet(), _case())       # its only question is external
    assert error is None and len(sheet["questions"]) == 1
    flag = next(i for i in issues if i["code"] == "answer_source_mostly_external")
    assert flag["answerable"] == 0 and flag["total"] == 1
    assert inquiry._status([flag]) == "OK"
    s = _sheet()
    s["questions"][0]["answer_source"] = "case_file"
    _, issues, _ = inquiry.validate(s, _case())
    assert not any(i["code"] == "answer_source_mostly_external" for i in issues)


def _reply(sheet):
    return {"model": "deepseek-flash", "choices": [{"message": {"content": json.dumps(sheet, ensure_ascii=False)},
                                                    "finish_reason": "stop"}]}


def _coded(n):
    s = _sheet()
    s["questions"][0]["why_material_cn"] = "、".join(f"field_{i}_btc" for i in range(n))
    return s


def test_code_tokens_are_flagged_per_text_and_counted():
    s = _sheet()
    s["questions"][0]["question_cn"] = ("L3.expiry_walls 的 pin 与 NO_MOVE、hour_profile、quiet 时段，"
                                        "1792675800000 与 0.023391812865497075，overlap 23.4M ms；"
                                        "BTC-23OCT26-83000-C 的 IV、Gamma、M-DIE、0DTE 与 Put 保留")
    sheet, issues, _ = inquiry.validate(s, _case())
    tokens = sheet["questions"][0]["code_tokens"]
    assert {"L3.expiry_walls", "NO_MOVE", "hour_profile", "quiet", "1792675800000",
            "0.023391812865497075", "23.4M ms"} <= set(tokens)
    assert not {"BTC", "IV", "Gamma", "Put", "pin", "DIE", "DTE", "M-DIE"} & set(tokens)
    assert inquiry.metrics(sheet, issues)["code_tokens"] == len(tokens)


def test_code_rewrite_is_adopted_only_when_it_has_fewer_codes():
    calls = []
    replies = [_coded(20), _coded(2)]
    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        return _reply(replies[len(calls) - 1])
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert len(calls) == 2 and "机器码" in calls[1]["messages"][-1]["content"]
    assert result["code_repair"] == {"tried": True, "adopted": True, "before": 20, "after": 2,
                                     "rewrite_code_tokens": 2, "rewrite_questions": 1}
    assert [a["repair"] for a in result["attempts"]] == [False, "code"]
    assert not calls[1]["messages"][-1]["content"].startswith("上面的输出未通过格式检查")
    assert result["metrics"]["code_tokens"] == 2

    calls.clear()
    replies[:] = [_coded(20), _coded(25)]
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["code_repair"]["adopted"] is False and result["metrics"]["code_tokens"] == 20


def test_failed_code_rewrite_keeps_the_first_sheet_and_clean_sheets_are_not_rewritten():
    calls = []
    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        return _reply(_coded(20)) if len(calls) == 1 else {"choices": [{"message": {"content": "not json"}}]}
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert len(calls) == 2 and result["status"] in {"OK", "PARTIAL"} and result["metrics"]["code_tokens"] == 20
    assert result["code_repair"]["adopted"] is False

    calls.clear()
    result = inquiry.run(_case(), api_key="k", model="m", transport=lambda *a, **k: calls.append(1) or _reply(_sheet()))
    assert len(calls) == 1 and "code_repair" not in result and result["metrics"]["code_tokens"] == 0


def test_transport_error_on_the_code_rewrite_keeps_the_first_sheet():
    calls = []
    def transport(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError("slow")
        return _reply(_coded(20))
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["sheet"] is not None and result["attempts"][-1] == {"error": "TimeoutError", "repair": "code"}
    assert result["code_repair"]["rewrite_error"] == "transport_error" and result["code_repair"]["adopted"] is False


def test_repeat_questions_are_flagged_not_dropped():
    s = _sheet()
    second = json.loads(json.dumps(s["questions"][0]))
    second["id"], second["priority"] = "K2", "medium"
    s["questions"].append(second)
    sheet, issues, _ = inquiry.validate(s, _case())
    assert [q["id"] for q in sheet["questions"]] == ["K1", "K2"]
    assert sheet["questions"][1]["possible_duplicate_of"] == "K1"
    assert inquiry.metrics(sheet, issues)["possible_duplicates"] == 1


def test_the_model_reads_the_reading_view_and_numbers_trace_against_it():
    case = _case()
    case["layers"]["L6"] = [{"ref": "L6.session_clock", "asof_ms": 1792648777645,
                             "values": {"us_open_ms": 1792675800000, "overlap_ms": 23400000, "factor": 1.1054191920767398}}]
    case["refs"]["L6.session_clock"] = {"layer": "L6", "index": 0}
    seen = []
    s = _sheet()
    s["fact_brief"]["layers"][6]["facts"] = [{"text_cn": "美股开盘 2026-10-22 13:30 UTC，重叠 6.5 小时，时段倍数 1.105",
                                              "refs": ["L6.session_clock"]}]
    def transport(key, model, request, timeout, endpoint=None):
        seen.append(request["messages"][1]["content"])
        return _reply(s)
    result = inquiry.run(case, api_key="k", model="m", transport=transport)
    assert "1792675800000" not in seen[0] and "2026-10-22 13:30 UTC" in seen[0] and "6.5 小时" in seen[0]
    assert "1.1054191920767398" not in seen[0]
    fact = result["sheet"]["fact_brief"]["layers"][6]["facts"][0]
    assert "untraced_numbers" not in fact and "code_tokens" not in fact
    assert result["reading_view"] == "inquiry_reading@0.1" and result["chinese_keys"] is True
    assert case["layers"]["L6"][0]["values"]["us_open_ms"] == 1792675800000      # the caller's case is untouched


def test_negated_odds_wording_is_a_restatement_not_a_claim():
    assert inquiry.forbidden("冻结风险均值缺失，不能按期望值对候选排序") is None
    assert inquiry.forbidden("程序未给胜率") is None
    assert inquiry.forbidden("这一侧期望值为正") == "期望值"
    assert inquiry.forbidden("不缺数据，建议承保") == "建议承保"
    assert inquiry.code_tokens("QQQ 与 VXN、Beta、skew、forward、Space 门") == []


def test_a_null_reply_is_repaired_not_crashed():
    calls = []
    def transport(*a, **k):
        calls.append(1)
        return {"choices": [{"message": {"content": "null"}, "finish_reason": "stop"}]} if len(calls) == 1 else _reply(_sheet())
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert len(calls) == 2 and result["sheet"] is not None and result["attempts"][1]["repair"] == "structural"
    result = inquiry.run(_case(), api_key="k", model="m",
                         transport=lambda *a, **k: {"choices": [{"message": {"content": "null"}}]})
    assert result["status"] == "REJECTED"


def test_a_cleaner_rewrite_that_loses_questions_is_not_adopted():
    first = _coded(20)
    second = json.loads(json.dumps(first["questions"][0]))
    second["id"], second["type"] = "K2", "mechanism"
    second["premises"] = [{"claim_cn": "现价距短腿 0.50%", "refs": ["L1.liability"]}]
    first["questions"].append(second)
    replies = [first, _sheet()]          # the rewrite has no codes but one question fewer
    calls = []
    def transport(*a, **k):
        calls.append(1)
        return _reply(replies[len(calls) - 1])
    result = inquiry.run(_case(), api_key="k", model="m", transport=transport)
    assert result["metrics"]["questions"] == 2 and result["metrics"]["code_tokens"] == 40
    assert result["code_repair"]["adopted"] is False and result["code_repair"]["rewrite_questions"] == 1


def test_entry_times_and_layer_quality_notes_trace():
    case = _case()
    case["layers"]["L3"][0]["asof_ms"] = 1792628820000          # 2026-10-22 00:27 UTC
    case["layers"]["L3"][0]["label_cn"] = "近 72 小时本期墙"
    s = _sheet()
    s["fact_brief"]["layers"][3]["facts"] = [{"text_cn": "近 72 小时本期墙取自 2026-10-22 00:27 UTC", "refs": ["L3.expiry_walls"]}]
    s["fact_brief"]["layers"][1]["quality_cn"] = "起赔 81500，距短腿 0.50%"
    def transport(*a, **k):
        return _reply(s)
    result = inquiry.run(case, api_key="k", model="m", transport=transport)
    assert "untraced_numbers" not in result["sheet"]["fact_brief"]["layers"][3]["facts"][0]
    assert not any(i["code"] == "untraced_number_flagged" and "quality_cn" in i["where"] for i in result["issues"])
