"""Consumption UI keeps current, historical and audit evidence in distinct layers."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402
from test_astra_wpem_frontend import _candidate, _workbench, Q1_CANONICAL, Q2_CANONICAL  # noqa: E402


def _accepted_display_result(candidate, *, snapshot_id=None, same_input=True, status="RESEARCH_OK"):
    snapshot = candidate["snapshot"]
    snapshot_id = snapshot_id or snapshot["snapshot_id"]
    evidence_input_hash = "b" * 64
    return {
        "status": status,
        "candidate_id": candidate["candidate_id"],
        "snapshot_id": snapshot_id,
        "review": {
            "status": status,
            "research_only": True,
            "production_attach_allowed": False,
            "execution_allowed": False,
            "candidate_id": candidate["candidate_id"],
            "reviewed_snapshot_id": snapshot_id,
            "response_protocol": "evidence_points@2.6.4",
            "display_binding": {
                "candidate_id": candidate["candidate_id"],
                "snapshot_id": snapshot_id,
                "evidence_input_hash": evidence_input_hash,
                "frozen_at_ms": 1790323200000,
                "reviewed_at_ms": 1790326800000,
                "receipt_id": "receipt-current",
                "same_input": same_input,
            },
            "q1": {"summary_cn": "PROGRAM_Q1_BASE", "llm_explanation": {
                "text_cn": "模型 Q1 原文：责任位置改善但主动流反证仍在。",
                "points": [{"text_cn": "模型 Q1 点一：责任位置改善但主动流反证仍在。", "evidence_refs": ["wpem.window"]}],
            }},
            "q2": {"summary_cn": "PROGRAM_Q2_BASE", "coverage_cn": "μ/M 缺失，不能断言补偿充分。", "llm_explanation": {
                "text_cn": "模型 Q2 原文：补偿变厚仍要受 μ/M 限制。",
                "points": [{"text_cn": "模型 Q2 点一：补偿变厚仍要受 μ/M 限制。", "evidence_refs": ["fee"]}],
            }},
            "decision_trace": {
                "binding": {"candidate_id": candidate["candidate_id"], "snapshot_id": snapshot_id},
                "layers": [{
                    "title_cn": "Actual LLM Input",
                    "produced_refs": ["wpem.window", "fee", "context.unused"],
                    "consumed_refs": ["wpem.window", "fee"],
                    "omitted": [{"ref": "context.unused", "reason_cn": "未被本次综合解释引用"}],
                }],
                "input_evidence": [
                    {"ref": "wpem.window", "label_cn": "严格窗口事实", "facts": ["严格窗口证据原文"], "scope": "same_candidate", "asof": 1790323200000, "source_family": "WPEM"},
                    {"ref": "fee", "label_cn": "费用事实", "facts": ["冻结费用原文"], "scope": "current_contract", "asof": 1790323200000, "source_family": "Economics"},
                    {"ref": "context.unused", "label_cn": "未引用背景", "facts": ["背景事实原文"], "scope": "background", "asof": 1790323200000, "source_family": "Context"},
                ],
                "points": [
                    {"question": "q1", "index": 0, "evidence_refs": ["wpem.window"]},
                    {"question": "q2", "index": 0, "evidence_refs": ["fee"]},
                ],
            },
        },
    }


def test_review_display_current_research_ok_becomes_main_explanation_without_production_attach():
    candidate = _candidate("put")
    candidate["review_display"] = {
        "current": _accepted_display_result(candidate),
        "historical": None,
        "reason_cn": "当前输入严格匹配",
        "data_asof_ms": 1790323200000,
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]

    assert "当前综合解释" in html
    assert "当前同版综合解释" in html
    assert Q1_CANONICAL in html and Q2_CANONICAL in html
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在" in html
    assert "模型 Q2 点一：补偿变厚仍要受 μ/M 限制" in html
    assert "当前无合格模型综合解释" not in html
    assert "PROGRAM_Q1_BASE" in html.split("程序事实底座", 1)[1]
    assert "未被本次综合解释引用" in html
    assert "依据标签" in html and "严格窗口事实" in html
    assert "系统已生成 3 条证据项" in html
    assert "3 条进入本次分析" in html
    assert "2 条被 Q1/Q2 直接引用" in html
    assert "未被本次综合解释引用" in html


def test_review_display_current_does_not_compare_binding_hash_to_wpem_evidence_hash():
    candidate = _candidate("put")
    assert candidate["wpem_evidence"]["evidence_hash"] != "b" * 64
    candidate["review_display"] = {
        "current": _accepted_display_result(candidate),
        "historical": None,
        "reason_cn": "selector 已严格比对 review_input.evidence_input",
        "data_asof_ms": 1790323200000,
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]

    assert "当前综合解释" in html
    assert "当前同版综合解释" in html
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在。" in html
    assert "当前无合格模型综合解释" not in html


def test_review_display_historical_demotes_to_folded_reference_and_program_fallback():
    candidate = _candidate("put")
    historical = _accepted_display_result(candidate, snapshot_id="old-snapshot")
    historical["review"]["decision_trace"]["input_evidence"].append({
        "ref": "contract",
        "facts": {
            "side": "put",
            "short_instrument": "OLD-BTC-PUT-SHORT",
            "long_instrument": "OLD-BTC-PUT-LONG",
            "expiry_ms": 1790400000000,
        },
    })
    historical["review"]["display_binding"]["frozen_at_ms"] = 1790323000000
    candidate["review_display"] = {
        "current": None,
        "historical": historical,
        "reason_cn": "当前 snapshot 已更新",
        "data_asof_ms": 1790323200000,
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    lead = html.split('<details class="wpem-stale-review"', 1)[0]

    assert "当前无合格模型综合解释" in lead
    assert "程序事实：当前责任位置比前窗更轻" in lead
    assert "模型 Q1 原文" not in lead
    assert "此前有效评审 · 历史参考" in html
    assert "当前 snapshot 已更新" in html
    assert "卖出腿 OLD-BTC-PUT-SHORT" in html
    assert "保护腿 OLD-BTC-PUT-LONG" in html
    assert "frozen" in html
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在" in html.split('<details class="wpem-stale-review"', 1)[1]
    assert "历史 Q1 · 原冻结时点" in html
    assert "历史参考 ·" in html


def test_review_display_rejected_current_body_never_enters_candidate_judgment():
    candidate = _candidate("put")
    rejected = _accepted_display_result(candidate, status="SEMANTIC_REJECTED")
    rejected["review"]["q1"]["llm_explanation"]["text_cn"] = "失败 Q1 正文不应显示"
    rejected["review"]["q2"]["llm_explanation"]["text_cn"] = "失败 Q2 正文不应显示"
    candidate["review_display"] = {"current": rejected, "historical": None, "reason_cn": "semantic rejected"}
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]

    assert "当前无合格模型综合解释" in html
    assert "失败 Q1 正文不应显示" not in html
    assert "失败 Q2 正文不应显示" not in html
    assert "程序事实：当前责任位置比前窗更轻" in html


def test_outer_rejected_inner_research_ok_never_displays_success_body():
    candidate = _candidate("put")
    rejected = _accepted_display_result(candidate)
    rejected["status"] = "SEMANTIC_REJECTED"
    rejected["reason_cn"] = "outer rejected"
    rejected["review"]["status"] = "RESEARCH_OK"
    rejected["review"]["reason_cn"] = "inner body must not promote outer failure"
    rejected["review"]["q1"]["llm_explanation"]["text_cn"] = "OUTER_REJECTED_INNER_OK_MARKER"
    rejected["review"]["q1"]["llm_explanation"]["points"][0]["text_cn"] = "OUTER_REJECTED_INNER_OK_MARKER"
    rejected["review"]["q2"]["llm_explanation"]["text_cn"] = "OUTER_REJECTED_INNER_OK_MARKER"
    rejected["review"]["q2"]["llm_explanation"]["points"][0]["text_cn"] = "OUTER_REJECTED_INNER_OK_MARKER"
    candidate["review_display"] = {"current": rejected, "historical": None, "reason_cn": "outer rejected"}
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": rejected,
        "last_completed_ts_ms": 1790326800000,
        "daily_http_budget": {},
    }
    rendered = render_workbench(view)["areas"]

    assert "OUTER_REJECTED_INNER_OK_MARKER" not in rendered["candidateArea"]
    assert "OUTER_REJECTED_INNER_OK_MARKER" not in rendered["manualReviewState"]
    assert "本次无合格评审正文" in rendered["manualReviewState"]
    assert "outer rejected" in rendered["manualReviewState"]


def test_frozen_replay_labels_current_binding_as_replay_not_live_market():
    candidate = _candidate("put")
    candidate["review_display"] = {
        "current": _accepted_display_result(candidate),
        "historical": None,
        "data_asof_ms": 1790323200000,
    }
    view = _workbench([candidate])
    view["preview"] = {"kind": "frozen_real_fixture", "mode": "FROZEN_REPLAY"}
    html = render_workbench(view)["areas"]["candidateArea"]

    assert "真实公开资料的冻结回放 · 非当前行情" in html
    assert html.count("真实公开资料的冻结回放 · 非当前行情") == 1
    assert "当前服务端候选" not in html.split("candidate-brief", 1)[0]
    assert "资料与解释依据时点" in html


def test_manual_review_state_links_current_body_to_its_single_candidate_home():
    candidate = _candidate("put")
    current = _accepted_display_result(candidate)
    candidate["review_display"] = {
        "current": current,
        "historical": None,
        "reason_cn": "当前输入严格匹配",
        "data_asof_ms": 1790323200000,
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": current,
        "last_completed_ts_ms": 1790326800000,
        "daily_http_budget": {},
    }
    areas = render_workbench(view)["areas"]
    html = areas["manualReviewState"]
    lead = html.split("<details", 1)[0]

    assert "当前同版回执已归入该保单的审计区" in lead
    assert 'href="#candidate-audit-candidate-put"' in lead
    assert 'id="candidate-audit-candidate-put"' in areas["candidateArea"]
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在。" not in html
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在。" in areas["candidateArea"]


def test_manual_review_state_labels_historical_accepted_as_folded_reference():
    candidate = _candidate("put")
    historical = _accepted_display_result(candidate, snapshot_id="old-snapshot")
    candidate["review_display"] = {
        "current": None,
        "historical": historical,
        "reason_cn": "snapshot identity changed",
        "data_asof_ms": 1790323200000,
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": historical,
        "last_completed_ts_ms": 1790326800000,
        "daily_http_budget": {},
    }
    html = render_workbench(view)["areas"]["manualReviewState"]
    lead = html.split("<details", 1)[0]

    assert "此前有效评审 · 历史参考" in lead
    assert "原因：当前 snapshot 已更新" in lead
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在。" not in lead
    assert "模型 Q1 点一：责任位置改善但主动流反证仍在。" in html


def test_trace_wpem_fragments_render_human_observed_points_before_raw_audit():
    candidate = _candidate("put")
    current = _accepted_display_result(candidate)
    current["review"]["q2"]["llm_explanation"]["points"][0]["evidence_refs"] = ["wpem.fragments"]
    current["review"]["decision_trace"]["input_evidence"].append({
        "ref": "wpem.fragments",
        "label_cn": "同保单历史观察片段",
        "source_path": "wpem.same_candidate_window.observed_points",
        "facts": {
            "scope_cn": "这些点只表示同 candidate 原始观察片段。",
            "observed_points": [
                {
                    "decision_asof_ms": 1790323000000,
                    "contract": {
                        "side": "put",
                        "short_instrument": "BTC-PUT-SHORT",
                        "long_instrument": "BTC-PUT-LONG",
                        "expiry_ms": 1790400000000,
                    },
                    "market": {"reference_price_usd": 100000},
                    "economics": {
                        "visible_credit_btc": 0.00038,
                        "fee_btc": 0.00001,
                        "net_credit_after_entry_fee_btc": 0.00037,
                    },
                },
                {
                    "decision_asof_ms": 1790323200000,
                    "contract": {
                        "side": "put",
                        "short_instrument": "BTC-PUT-SHORT",
                        "long_instrument": "BTC-PUT-LONG",
                        "expiry_ms": 1790400000000,
                    },
                    "market": {"reference_price_usd": 100100},
                    "economics": {
                        "visible_credit_btc": 0.0004,
                        "fee_btc": 0.00001,
                        "net_credit_after_entry_fee_btc": 0.00039,
                    },
                },
            ],
        },
        "relations": {
            "scope_cn": "只表示相邻 quote 的原始变化。",
            "latest_adjacent_quote_pair": {
                "prev_decision_asof_ms": 1790323000000,
                "curr_decision_asof_ms": 1790323200000,
                "reference_price_relation": "increased",
                "visible_credit_relation": "increased",
                "fee_relation": "unchanged",
                "net_credit_after_fee_relation": "increased",
                "same_contract_legs": True,
                "same_price_and_quote_basis": True,
            },
        },
        "qualification": {
            "delta_status": "NO_STRICT_DELTA",
            "path_status": "NO_PATH_DUE_TO_WINDOW_GAP",
            "window_status": "INSUFFICIENT",
            "packet_execution_allowed": False,
        },
    })
    candidate["review_display"] = {"current": current, "historical": None, "reason_cn": "当前输入严格匹配"}
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    fragment = html.split("已观察片段", 1)[1].split('<details class="ia-raw"', 1)[0]

    assert "局部同合同相邻比较不等于完整 Path" in fragment
    assert "已观察片段≠严格 Delta/Path" in fragment
    assert "<th>时间</th><th>标的价</th><th>信用</th><th>费用</th><th>净信用</th>" in fragment
    assert "2026/" in fragment
    assert "可见信用增加" in fragment and "净信用增加" in fragment
    assert "packet_execution_allowed" not in fragment
    assert "packet_execution_allowed" in html.split('<details class="ia-raw"', 1)[1]


def test_strict_window_and_observed_fragments_have_separate_scope_labels():
    candidate = _candidate("put")
    candidate["wpem_evidence"]["same_candidate_window"]["LevelDeltaPath"]["delta"]["changes"] = {
        "reference_price_usd_delta": 100,
        "visible_credit_btc_delta": 0.00002,
        "fee_btc_delta": -0.00001,
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]

    assert "严格窗口证据" in html
    assert "用于判断严格 Delta / Path 是否成立" in html
    assert "已观察片段" in html
    assert "局部同合同相邻比较，不等于完整 Path" in html
