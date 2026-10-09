"""Frontend rendering for auditable observation traces stays read-only and point-scoped."""
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402
from test_astra_wpem_frontend import _candidate, _workbench  # noqa: E402


def _trace_review(candidate):
    snapshot = candidate["snapshot"]
    return {
        "status": "RESEARCH_OK",
        "research_only": True,
        "production_attach_allowed": False,
        "execution_allowed": False,
        "candidate_id": snapshot["candidate_id"],
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "response_protocol": "evidence_points@2.6.4",
        "selection_origin": "program_guardrails",
        "applicability": "UNABLE_TO_JUDGE",
        "wpem_evidence": {
            "decision_asof_ms": 1790323200000,
            "current_cross_section": {
                "cross_section_rows": [{
                    "snapshot_id": snapshot["snapshot_id"],
                    "contract": {
                        "side": "put",
                        "short_strike_usd": 97000,
                        "long_strike_usd": 95000,
                        "expiry_ms": 1790400000000,
                    },
                }],
            },
        },
        "q1": {
            "summary_cn": "PROGRAM_Q1_SHOULD_BE_FOLDED",
            "llm_explanation": {
                "text_cn": "责任位置改善但主动流反证仍在。MAP 背景只作为条件。",
                "evidence_refs": ["wpem.window", "context.active_flow", "map.background.allocation"],
                "points": [
                    {
                        "text_cn": "责任位置改善但主动流反证仍在。",
                        "evidence_refs": ["wpem.window", "context.active_flow"],
                        "evidence": [{
                            "ref": "context.active_flow",
                            "source_path": "actual_input.market.active_flow",
                            "provided": {
                                "ref": "context.active_flow",
                                "group": "flow",
                                "label_cn": "主动流反证",
                                "facts": ["短窗主动卖出未消失", "<img src=x onerror=alert(1)>"] ,
                                "relations": ["与位置改善构成冲突"],
                                "asof": 1790323200000,
                                "scope": "当前冻结输入",
                                "basis": "fmz.flow.current",
                                "qualification": "局部可用",
                                "limitations": ["不能单独当作方向确认"],
                            },
                        }],
                    },
                    {
                        "text_cn": "MAP 背景只作为条件。",
                        "evidence_refs": ["map.background.allocation"],
                    },
                ],
            },
        },
        "q2": {
            "summary_cn": "PROGRAM_Q2_SHOULD_BE_FOLDED",
            "coverage_cn": "缺少风险参考，不能断言补偿充分。",
            "llm_explanation": {
                "text_cn": "补偿变薄需要与同保单窗口一起看。",
                "evidence_refs": ["wpem.compensation", "fee"],
                "points": [{
                    "text_cn": "补偿变薄需要与同保单窗口一起看。",
                    "evidence_refs": ["wpem.compensation", "fee"],
                }],
            },
        },
        "decision_trace": {
            "binding": {"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"]},
            "layers": [
                {
                    "layer": "data",
                    "title_cn": "Data",
                    "produced_refs": ["context.active_flow", "map.background.allocation"],
                    "consumed_refs": ["context.active_flow"],
                    "omitted": [{"ref": "context.funding", "reason_cn": "本点未引用，保留为审计背景"}],
                },
                {
                    "layer": "actual_llm_input",
                    "title_cn": "Actual LLM Input",
                    "produced_refs": ["wpem.window", "context.active_flow", "map.background.allocation", "wpem.compensation", "fee"],
                    "consumed_refs": ["wpem.window", "context.active_flow", "wpem.compensation", "fee"],
                    "omitted": [{"ref": "map.background.allocation", "reason_cn": "只作背景，不进入补偿取舍点"}],
                },
            ],
            "input_evidence": [
                {
                    "ref": "wpem.window",
                    "group": "wpem",
                    "label_cn": "同保单窗口",
                    "facts": ["责任位置较前窗改善"],
                    "relations": ["只解释同一候选变化"],
                    "asof": 1790323200000,
                    "window": {"lookback": "1h"},
                    "scope": "same_candidate",
                    "basis": "WPEM LevelDeltaPath",
                    "qualification": "SYNTHETIC_WINDOW_COVERED",
                    "limitations": ["窗口不足时只限制该结论"],
                    "source_path": "wpem.same_candidate_window",
                },
                {
                    "ref": "map.background.allocation",
                    "group": "allocation",
                    "label_cn": "ETF 慢背景",
                    "facts": ["慢背景偏支持，但不是确认"],
                    "relations": ["仅作条件背景"],
                    "asof": 1790319600000,
                    "scope": "background",
                    "basis": "btc_map.source_records[allocation]",
                    "qualification": "BACKGROUND_USABLE",
                    "limitations": ["低频资料不同步"],
                    "source_path": "btc_map.references.allocation",
                },
                {
                    "ref": "wpem.compensation",
                    "group": "wpem",
                    "label_cn": "补偿变化",
                    "facts": ["可见信用变薄"],
                    "relations": ["与责任改善方向相反"],
                    "asof": 1790323200000,
                    "scope": "same_candidate",
                    "basis": "WPEM compensation",
                    "qualification": "PARTIAL",
                    "limitations": ["缺少完整风险参考"],
                },
                {
                    "ref": "fee",
                    "group": "accounting",
                    "label_cn": "冻结费用",
                    "facts": ["费用按本次输入口径提供"],
                    "relations": ["只影响补偿净额"],
                    "asof": 1790323200000,
                    "scope": "current_contract",
                    "basis": "snapshot.fee",
                    "qualification": "局部可用",
                    "limitations": ["不是成交回执"],
                },
            ],
            "audit_only": [{"field": "source_hash", "reason_cn": "只用于审计下钻，不进入模型综合"}],
            "points": [
                {"question": "q1", "index": 0, "text_cn": "责任位置改善但主动流反证仍在。", "evidence_refs": ["wpem.window", "context.active_flow"]},
                {"question": "q1", "index": 1, "text_cn": "MAP 背景只作为条件。", "evidence_refs": ["map.background.allocation"]},
                {"question": "q2", "index": 0, "text_cn": "补偿变薄需要与同保单窗口一起看。", "evidence_refs": ["wpem.compensation", "fee"]},
            ],
        },
    }


def test_evidence_points_protocol_renders_point_scoped_trace_without_fetching():
    candidate = _candidate()
    review = _trace_review(candidate)
    review["decision_trace"]["points"][0]["evidence_refs"].append("trace.only.extra")
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": {
            "status": "RESEARCH_OK",
            "review": review,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "side": "put",
        }
    }

    html = render_workbench(view)["areas"]["manualReviewState"]

    assert "责任位置改善但主动流反证仍在" in html
    assert "MAP 背景只作为条件" in html
    assert "这版解释的证据消费" in html
    assert "系统已生成" in html and "进入本次分析" in html and "被本次解释引用" in html
    assert "本点未引用，保留为审计背景" in html
    assert "同保单窗口" in html and "主动流反证" in html and "ETF 慢背景" in html
    assert "实际模型输入证据清单" in html
    assert "只用于审计下钻，不进入模型综合" in html
    assert "安全与边界约束" in html
    assert "模型选择" not in html and "LLM 选择" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x" not in html

    point_zero = html.split("责任位置改善但主动流反证仍在", 1)[1].split("MAP 背景只作为条件", 1)[0]
    assert "wpem.window" in point_zero and "context.active_flow" in point_zero
    assert "map.background.allocation" not in point_zero
    assert "trace.only.extra" not in point_zero


def test_old_grounded_explanation_shape_still_renders_legacy_reference_fold():
    candidate = _candidate()
    snapshot = candidate["snapshot"]
    review = {
        "status": "RESEARCH_OK",
        "research_only": True,
        "production_attach_allowed": False,
        "candidate_id": snapshot["candidate_id"],
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "response_protocol": "bounded_claim_ids@2.6.2",
        "q1": {"summary_cn": "PROGRAM_Q1", "llm_explanation": {
            "text_cn": "旧协议 Q1 解释仍可读。",
            "evidence_refs": ["wpem.window"],
            "evidence": [{"ref": "wpem.window", "provided": {"path_cn": "旧协议窗口证据。"}}],
        }},
        "q2": {"summary_cn": "PROGRAM_Q2", "llm_explanation": {
            "text_cn": "旧协议 Q2 解释仍可读。",
            "evidence_refs": ["fee"],
            "evidence": [{"ref": "fee", "provided": {"source_fee": {"fee": {"amount_btc": 0.0001}}}}],
        }},
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {"last_result": {"status": "RESEARCH_OK", "review": review, "candidate_id": candidate["candidate_id"], "snapshot_id": snapshot["snapshot_id"], "side": "put"}}

    html = render_workbench(view)["areas"]["manualReviewState"]

    assert "旧协议 Q1 解释仍可读" in html
    assert "本点依据" in html
    assert "旧协议窗口证据" in html
    assert "查看原始信息" in html


def test_failed_latest_result_keeps_previous_valid_receipt_folded_as_history():
    candidate = _candidate()
    previous_review = _trace_review(candidate)
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": {"status": "LLM_ERROR", "candidate_id": candidate["candidate_id"], "snapshot_id": candidate["snapshot"]["snapshot_id"]},
        "previous_valid_result": {
            "status": "RESEARCH_OK",
            "historical_only": True,
            "historical_completed_ts_ms": 1790323300000,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "side": "put",
            "review": previous_review,
        },
    }

    html = render_workbench(view)["areas"]["manualReviewState"]

    assert "本次无合格评审正文" in html
    assert "此前有效评审 · 历史参考" in html
    assert "不会把它回填为当前结果" in html
    assert "责任位置改善但主动流反证仍在" in html
    assert "这版解释的证据消费" in html
    assert "实际模型输入证据清单" in html


def test_root_normalized_explanation_fallback_does_not_recurse_into_program_basis():
    candidate = _candidate()
    review = _trace_review(candidate)
    root_explanation = {
        "q1": review["q1"]["llm_explanation"],
        "q2": review["q2"]["llm_explanation"],
    }
    review["llm_explanation"] = root_explanation
    review["normalized"] = {
        "llm_explanation": root_explanation,
        "decision_trace": review["decision_trace"],
    }
    review["q1"] = {"summary_cn": "PROGRAM_Q1_ONLY"}
    review["q2"] = {"summary_cn": "PROGRAM_Q2_ONLY"}
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": {
            "status": "RESEARCH_OK",
            "review": review,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "side": "put",
        }
    }

    html = render_workbench(view)["areas"]["manualReviewState"]

    assert "责任位置改善但主动流反证仍在" in html
    assert "程序事实底座" in html
    program_basis = html.split("程序事实底座", 1)[1]
    assert "PROGRAM_Q1_ONLY" in program_basis
    assert "PROGRAM_Q2_ONLY" in program_basis
    assert "trace-point-list" not in program_basis
    assert html.count("程序事实底座") == 1


def test_failed_result_shows_frozen_input_trace_without_failed_llm_body():
    candidate = _candidate()
    review = _trace_review(candidate)
    review["status"] = "SEMANTIC_REJECTED"
    review["reason_cn"] = "引用遗漏：fee 未被模型点采用"
    review["raw_response"] = "原始失败回复不应显示"
    review["llm_explanation"] = {
        "q1": {"text_cn": "失败模型正文不应显示"},
        "q2": {"text_cn": "失败模型正文二不应显示"},
    }
    review["q1"]["llm_explanation"] = {"text_cn": "失败 Q1 正文不应显示"}
    review["q2"]["llm_explanation"] = {"text_cn": "失败 Q2 正文不应显示"}
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "last_result": {
            "status": "SEMANTIC_REJECTED",
            "reason_cn": "外层失败原因不优先",
            "raw_response": "外层原始失败回复不应显示",
            "review": review,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "side": "put",
        }
    }

    html = render_workbench(view)["areas"]["manualReviewState"]

    assert "本次无合格评审正文" in html
    assert "引用遗漏：fee 未被模型点采用" in html
    assert "本次冻结输入审计（解释未采纳）" in html
    assert "实际模型输入证据清单" in html
    assert "同保单窗口" in html
    assert "失败模型正文不应显示" not in html
    assert "失败 Q1 正文不应显示" not in html
    assert "原始失败回复不应显示" not in html
    assert "Q1 · 这张保单相对适配在哪里" not in html
