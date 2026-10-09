import json
import shutil
import subprocess
from pathlib import Path
import sys
import pytest

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"

QUOTE_HASH = "1" * 64
RISK_HASH = "2" * 64
PROJECTION_HASH = "3" * 64
REVIEW_EVIDENCE_HASH = "4" * 64
REVIEW_CATALOG_HASH = "5" * 64
FRONTEND_SOURCE_HASH = "6" * 64
EVIDENCE_SOURCE_HASH = "7" * 64
CURRENT_PACKET_HASH = "8" * 64
PEER_PACKET_HASH = "9" * 64
OLD_PEER_PACKET_HASH = "a" * 64
BINDING_WINDOW_HASH = "0" * 64
Q1_CANONICAL = "Q1 · 当前相对适配在哪里，主要瑕疵是什么？"
Q2_CANONICAL = "Q2 · 当前补偿是否值得交换这些风险？"


def _candidate(side="put", *, with_wpem=True):
    title = side.upper()
    candidate_id = f"candidate-{side}"
    snapshot_id = f"snapshot-{side}"
    candidate = {
        "side": side,
        "candidate_id": candidate_id,
        "status": "snapshot",
        "review_gate": {"enabled": False, "reasons": ["manual_review_service_not_configured"]},
        "snapshot": {
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
            "side": side,
            "data_identity": {"kind": "live_public_capture"},
            "contract": {
                "side": side,
                "short_instrument": f"BTC-{title}-SHORT",
                "long_instrument": f"BTC-{title}-LONG",
                "quantity_btc": 0.1,
                "short_strike_usd": 97000,
                "long_strike_usd": 95000,
                "width_usd": 2000,
                "expiry_ms": 1790400000000,
                "remaining_to_expiry_ms": 86400000,
            },
            "market": {"reference_price_usd": 100000, "reference_source": "deribit_index", "quote": {}},
            "fee": {"amount_btc": 0.0000175, "entry_fee_btc": 0.00001, "delivery_fee_btc": 0.0000075},
            "economics": {
                "premium_spread_btc": 0.0004,
                "theoretical_two_leg_fee_btc": 0.00001,
                "total_hold_fee_usd_at_spot": 1.75,
                "delivery_fee_usd_at_spot": 0.75,
                "net_credit_after_entry_fee_btc": 0.00039,
                "net_credit_after_fee_btc": 0.0003825,
                "mu_ref_btc": None,
                "reference_margin_btc": None,
            },
            "risk": {"status": "not_configured"},
            "liability": {"payout_table": [{"settlement_usd": 97000, "payout_btc": 0.0}]},
        },
    }
    if with_wpem:
        candidate.update({
            "wpem_summary": {
                "path_cn": "不应在主视图重复的路径摘要。",
                "compensation_cn": "不应在主视图重复的补偿摘要。",
                "breaks_cn": ["真实原生窗口不足，不能当作胜率。", "同候选增量缺少断点。"],
                "path_classification": "single_direction_favorable_relief",
                "window_status": "SYNTHETIC_WINDOW_COVERED",
                "delta_status": "STRICT_SAME_CANDIDATE_DELTA",
            },
            "wpem_evidence": {
                "schema": "wpem_review_evidence@1.0.0",
                "evidence_hash": "e" * 64,
                "same_candidate_window": {
                    "source_window_evidence_hash": "w" * 64,
                    "qualification": {"status": "SYNTHETIC_WINDOW_COVERED"},
                    "coverage_summary": {
                        "schema": "wpem_window_coverage_summary@1.0.0",
                        "source_field": "window_evidence.coverage",
                        "copied_fields": [
                            "requested_window_ms",
                            "actual_coverage_ms",
                            "coverage_start_ms",
                            "coverage_end_ms",
                            "cutoff_ms",
                            "point_count",
                            "qualified_points",
                            "native_qualified_points",
                            "max_gap_ms",
                            "status",
                            "evidence_kind",
                        ],
                        "windows": [
                            {
                                "requested_window_ms": 900000,
                                "actual_coverage_ms": 900000,
                                "coverage_start_ms": 1790322300000,
                                "coverage_end_ms": 1790323200000,
                                "cutoff_ms": 1790323200000,
                                "point_count": 3,
                                "qualified_points": 2,
                                "native_qualified_points": 0,
                                "max_gap_ms": 300000,
                                "status": "SYNTHETIC_WINDOW_COVERED",
                                "evidence_kind": "synthetic_fixture",
                            },
                            {
                                "requested_window_ms": 3600000,
                                "actual_coverage_ms": 3600000,
                                "coverage_start_ms": 1790319600000,
                                "coverage_end_ms": 1790323200000,
                                "cutoff_ms": 1790323200000,
                                "point_count": 6,
                                "qualified_points": 4,
                                "native_qualified_points": 0,
                                "max_gap_ms": 600000,
                                "status": "INSUFFICIENT_WINDOW",
                                "evidence_kind": "synthetic_fixture",
                            },
                        ],
                        "policy": {
                            "raw_point_refs_excluded": True,
                            "raw_source_bytes_excluded": True,
                            "private_source_blobs_excluded": True,
                            "does_not_change_window_qualification": True,
                            "does_not_grant_execution": True,
                            "does_not_change_financial_or_review_gates": True,
                        },
                    },
                    "LevelDeltaPath": {
                        "level": {"current_level": "责任位置较远"},
                        "delta": {"status": "STRICT_SAME_CANDIDATE_DELTA", "credit_delta_btc": -0.00001},
                        "path": {"status": "PATH_OBSERVED", "classification": "single_direction_favorable_relief"},
                        "responsibility": {"status": "known"},
                    },
                    "source_method_breaks": {"method_break": False, "candidate_rotation": False},
                    "dependency_diagnostics": {"shared_source_reads": 2, "independent_updates": 1},
                },
                "current_cross_section": {
                    "cross_section_hash": "c" * 64,
                    "source_kind": "live_public_capture",
                    "cross_section_rows": [
                        {
                            "role": "CURRENT",
                            "candidate_id": candidate_id,
                            "snapshot_id": snapshot_id,
                            "decision_asof_ms": 1790323200000,
                            "packet_hash": CURRENT_PACKET_HASH,
                            "comparability": "CURRENT",
                            "same_decision_asof": True,
                            "raw_row_hash": "b" * 64,
                        },
                        {
                            "role": "PEER",
                            "candidate_id": "candidate-call-peer",
                            "snapshot_id": "snapshot-call-peer",
                            "decision_asof_ms": 1790323200000,
                            "packet_hash": PEER_PACKET_HASH,
                            "comparability": "COMPARABLE_PEER",
                            "same_decision_asof": True,
                            "not_used_for_window": True,
                            "not_ranked_or_scored": True,
                            "source": "peer_snapshot",
                            "raw_row_hash": "d" * 64,
                        },
                        {
                            "role": "PEER",
                            "candidate_id": "candidate-old-call-peer",
                            "snapshot_id": "snapshot-old-call-peer",
                            "decision_asof_ms": 1790322900000,
                            "packet_hash": OLD_PEER_PACKET_HASH,
                            "comparability": "NON_COMPARABLE_SNAPSHOT_ASOF",
                            "same_decision_asof": False,
                            "not_used_for_window": True,
                            "not_ranked_or_scored": True,
                            "excluded_reason": "asof mismatch",
                            "raw_row_hash": "f" * 64,
                        },
                    ],
                },
                "counter_catalog_gap_reassess": {
                    "source_catalog_hash": "a" * 64,
                    "source_response_protocol": "bounded_claim_ids@2.5.0-candidate",
                    "status": "CATALOG_GAP",
                },
                "risk_reference_status": {"mu_M_null_reason": "REFERENCE_ANALYSIS_MISSING"},
                "source_bindings": {
                    "source_records": ["record-alpha", "record-beta"],
                    "source_manifest_hashes": ["b" * 64],
                },
            },
            "current_questions": {
                "source_kind": "deterministic_program_no_llm",
                "q1": {
                    "title_cn": "内部路径标题不应成为主标题",
                    "summary_cn": "程序事实：当前责任位置比前窗更轻。",
                    "window_attribution_cn": "归因来自同候选窗口，不是市场趋势。",
                },
                "q2": {
                    "title_cn": "内部补偿标题不应成为主标题",
                    "summary_cn": "程序事实只比较可见信用、费用和责任。",
                    "window_tradeoff_cn": "信用下降同时责任缓解，需要人工取舍。",
                    "observable_changes_cn": ["等待下一笔同候选报价", "观察窗口是否连续"],
                    "waiting_cost_cn": "等待可能丢失当前可见信用。",
                },
                "audit": {"mu_M_null_reason": "REFERENCE_ANALYSIS_MISSING"},
            },
            "review_validity": {"status": "NOT_RUN", "reason_cn": "尚未运行当前同版 LLM。"},
        })
    return candidate


def _workbench(candidates=None):
    return {
        "schema": "astra_workbench@2.0.0",
        "updated_at_ms": 1790323200000,
        "release_identity": {
            "release_id": "v2.5.1-wpem-consumer-rc2-local",
            "status": "local_not_deployed",
            "components": [
                {
                    "component": "frontend",
                    "candidate_version": "v2.5.1-wpem-consumer-rc2-local",
                    "source_of_truth": "deploy/signal_audit/frontend/app.js",
                    "source_hash": FRONTEND_SOURCE_HASH,
                    "status": "local_not_deployed",
                },
                {
                    "component": "evidence",
                    "candidate_version": "wpem_review_evidence@1.0.1",
                    "source_of_truth": "tools/astra_wpem_review_evidence_v1.py",
                    "source_hash": EVIDENCE_SOURCE_HASH,
                    "status": "CURRENT",
                },
            ],
            "manifest_hash": "r" * 64,
        },
        "view": {
            "release_identity": {
                "release_id": "stale-view-release-should-not-render",
                "status": "stale_view",
                "components": [
                    {"name": "frontend", "hash": "stale-view"},
                ],
                "manifest_hash": "m" * 64,
            }
        },
        "space_gate": {"state": "STRUCTURE_BLOCKED", "source": "producer_native_space_gate_missing"},
        "manual_review_state": {"last_result": {}, "daily_http_budget": {}},
        "candidates": candidates if candidates is not None else [_candidate("put"), _candidate("call")],
    }


def _main_candidate_view(html):
    lead = html.split('<details class="wpem-audit"', 1)[0]
    return lead.split('<details class="candidate-details"', 1)[0]


def test_program_questions_keep_core_with_compact_wpem_readout_and_no_mu_placeholders():
    html = render_workbench(_workbench([_candidate("put")]))["areas"]["candidateArea"]
    main_html = _main_candidate_view(html)
    assert Q1_CANONICAL in main_html
    assert Q2_CANONICAL in main_html
    assert "程序事实：当前责任位置比前窗更轻" in main_html
    assert "归因来自同候选窗口，不是市场趋势" in main_html
    assert "信用下降同时责任缓解" in main_html
    assert "等待下一笔同候选报价" in main_html
    # Existing summary fields now belong to the compact WPEM readout, while
    # the two question bodies and the complete source audit remain intact.
    readout = main_html.split('class="wpem-readout"', 1)[1].split("</dl>", 1)[0]
    assert "不应在主视图重复的路径摘要" in readout
    assert "不应在主视图重复的补偿摘要" in readout
    assert "建仓费用" in main_html
    assert "建仓费用 f_entry" in html
    assert "0.00001 BTC" in main_html
    assert "两腿理论成交手续费，未成交" in main_html
    assert "总费用 f" not in main_html
    assert "冻结赔付 μ_ref" not in main_html
    assert "参考余量 M_ref" not in main_html
    assert "预计赔付未量化" not in html
    assert "正 EV" not in html
    assert "WPEM 证据、窗口与风险审计" in html


def test_breaks_array_renders_as_explicit_limit_list_and_statuses_stay_in_audit():
    html = render_workbench(_workbench([_candidate("put")]))["areas"]["candidateArea"]
    main_html = _main_candidate_view(html)
    assert "影响当前判断的限制" in main_html
    assert "<li>真实原生窗口不足，不能当作胜率</li>" in main_html
    assert "<li>同候选增量缺少断点</li>" in main_html
    assert "窗口/增量状态" not in main_html
    assert "窗口/增量状态" in html
    assert "模拟窗口覆盖" in html
    assert "15m / 1h 覆盖诊断" in html
    assert "实际跨度 15 分钟" in html
    assert "原生合格点 0" in html
    assert "max gap 5 分钟" in html
    assert "WPEM 截面、Level / Delta / Path" in html
    assert "credit_delta_btc=-0.00001" in html
    assert "截面行身份与可比性" in html
    assert "candidate-old-call-peer" in html
    assert "snapshot-old-call-peer" in html
    assert OLD_PEER_PACKET_HASH in html
    assert "NON_COMPARABLE_SNAPSHOT_ASOF" in html
    assert "asof mismatch" in html
    assert "依赖与断点" in html
    assert "shared_source_reads=2" in html
    assert "record-alpha" in html
    assert "w" * 64 in html
    assert "c" * 64 in html
    assert "有限目录容量不足" in html


def test_root_release_components_array_renders_readably_in_folded_audit():
    html = render_workbench(_workbench([_candidate("put")]))["areas"]["candidateArea"]
    assert "[object Object]" not in html
    assert "frontend candidate_version=v2.5.1-wpem-consumer-rc2-local" in html
    assert FRONTEND_SOURCE_HASH in html
    assert "evidence candidate_version=wpem_review_evidence@1.0.1" in html
    assert EVIDENCE_SOURCE_HASH in html
    assert "stale-view-release-should-not-render" not in html


def test_replay_preview_dominates_map_identity_and_unknown_map_is_not_current():
    view = _workbench([_candidate("put", with_wpem=False)])
    view["preview"] = {"kind": "public_frozen_replay", "mode": "PUBLIC_FROZEN_REPLAY"}
    view["btc_map"] = {
        "map_id": "map-replay",
        "presentation": {"mode": "CURRENT"},
        "current_price_usd": 100000,
        "cutoff_at_ms": 1790323200000,
    }
    html = render_workbench(view)["areas"]["btcMap"]
    assert "冻结回放 · 全局 MAP" in html
    assert "冻结现价 100,000 USD" in html
    assert "当前资料 · 全局 MAP" not in html
    assert "当前现价" not in html

    live_view = _workbench([_candidate("put", with_wpem=False)])
    live_view["btc_map"] = {"map_id": "map-unknown", "current_price_usd": 100000, "cutoff_at_ms": 1790323200000}
    html = render_workbench(live_view)["areas"]["btcMap"]
    assert "身份未确认 · 全局 MAP" in html
    assert "资料现价 100,000 USD" in html
    assert "当前资料 · 全局 MAP" not in html


def test_current_review_takes_precedence_over_program_questions_and_receipt_is_folded():
    candidate = _candidate("put")
    candidate["review_validity"] = {"status": "CURRENT", "checked_at_ms": 1790323200000}
    candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": candidate["snapshot"]["snapshot_id"],
        "response_protocol": "bounded_claim_ids@2.5.0-candidate",
        "requested_model": "deepseek-v4-flash",
        "claim_catalog_hash": REVIEW_CATALOG_HASH,
        "applicability": "EXPLAINED",
        "selected_claim_ids": [f"claim.wpem.{index}" for index in range(1, 5)],
        "selected_counter_ids": [f"counter.wpem.{index}" for index in range(1, 5)],
        "selected_gap_ids": [f"gap.wpem.{index}" for index in range(1, 3)],
        "selected_reassess_condition_ids": [f"reassess.wpem.{index}" for index in range(1, 3)],
        "review_validity": {
            "schema": "wpem_product_review_validity@1.0.0",
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "quote_hash": QUOTE_HASH,
            "quote_timestamp_ms": 1790323200000,
            "risk_hash": RISK_HASH,
            "window_evidence_hash": BINDING_WINDOW_HASH,
            "review_evidence_hash": REVIEW_EVIDENCE_HASH,
            "review_input_version": "review_input@2.5.0",
            "projection_hash": PROJECTION_HASH,
            "llm_protocol": "bounded_claim_ids@2.5.0-candidate",
            "model": "deepseek-v4-flash",
            "cooldown_extends_validity": False,
        },
        "q1": {
            "summary_cn": "LLM 同版：路径可以解释。",
            "window_attribution_cn": "LLM 同版窗口归因",
        },
        "q2": {
            "title_cn": "旧标题不应替换固定两问",
            "summary_cn": "LLM 同版：补偿仍需人工判断。",
            "window_tradeoff_cn": "LLM 同版报价取舍。",
        },
    }
    view = _workbench([candidate])
    view["__test_state"] = {"selectedMapCandidateKey": "put"}
    rendered = render_workbench(view)["areas"]
    candidate_html = rendered["candidateArea"]
    manual_html = rendered["manualReviewState"]
    assert Q1_CANONICAL in candidate_html
    assert Q2_CANONICAL in candidate_html
    assert "LLM 同版窗口归因" in candidate_html
    assert "LLM 同版报价取舍" in candidate_html
    assert "程序事实：当前责任位置比前窗更轻" not in candidate_html
    assert QUOTE_HASH in candidate_html
    assert RISK_HASH in candidate_html
    assert PROJECTION_HASH in candidate_html
    assert BINDING_WINDOW_HASH in candidate_html
    assert REVIEW_EVIDENCE_HASH in candidate_html
    assert REVIEW_CATALOG_HASH in candidate_html
    assert "a" * 64 in candidate_html
    assert "w" * 64 in candidate_html
    assert "Review binding window" in candidate_html
    assert "Current window evidence" in candidate_html
    assert "Review binding evidence" in candidate_html
    assert "Current review evidence" in candidate_html
    assert "deepseek-v4-flash" in candidate_html
    assert "claim.wpem.1" in candidate_html
    assert "reassess.wpem.2" in candidate_html
    assert "查看当前保单同版回执原文与目录" in manual_html
    assert "服务端回执" in manual_html


def test_candidate_stale_status_dominates_attached_review_binding_current():
    candidate = _candidate("put")
    candidate["review_validity"] = {"status": "STALE", "reason_cn": "snapshot changed"}
    candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": candidate["snapshot"]["snapshot_id"],
        "response_protocol": "bounded_claim_ids@2.5.0-candidate",
        "applicability": "EXPLAINED",
        "review_validity": {
            "status": "CURRENT",
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "quote_hash": QUOTE_HASH,
            "risk_hash": RISK_HASH,
            "window_evidence_hash": BINDING_WINDOW_HASH,
            "review_evidence_hash": REVIEW_EVIDENCE_HASH,
            "projection_hash": PROJECTION_HASH,
            "llm_protocol": "bounded_claim_ids@2.5.0-candidate",
            "model": "deepseek-v4-flash",
        },
        "summary_cn": "旧同版正文不应提升为当前。",
        "q1": {"summary_cn": "旧 Q1 绑定正文"},
        "q2": {"summary_cn": "旧 Q2 绑定正文"},
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    leading = html.split('<details class="wpem-stale-review"', 1)[0]
    assert "当前综合解释" not in html
    assert Q1_CANONICAL in leading
    assert "程序事实：当前责任位置比前窗更轻" in leading
    assert "旧评审已过期，仅折叠保留" in html
    assert "snapshot changed" in html
    assert "旧同版正文不应提升为当前" not in leading
    assert BINDING_WINDOW_HASH in html
    assert "w" * 64 in html


def test_research_ok_renders_as_research_success_without_current_attach():
    candidate = _candidate("put")
    candidate["review_validity"] = {"status": "CURRENT", "checked_at_ms": 1790323200000}
    candidate["review"] = {
        "status": "RESEARCH_OK",
        "production_attach_allowed": False,
        "reviewed_snapshot_id": candidate["snapshot"]["snapshot_id"],
        "response_protocol": "bounded_claim_ids@2.5.0-candidate",
        "applicability": "EXPLAINED",
        "summary_cn": "研究正文语义通过。",
        "q1": {"summary_cn": "研究 Q1 正文"},
        "q2": {"summary_cn": "研究 Q2 正文"},
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {"last_result": {**candidate["review"], "candidate_id": candidate["candidate_id"], "side": "put", "review": candidate["review"]}}
    rendered = render_workbench(view)["areas"]
    assert "当前综合解释" not in rendered["candidateArea"]
    assert "研究复核完成 · 未生产附着" in rendered["manualReviewState"]
    assert "研究正文语义通过" in rendered["manualReviewState"]
    assert "本次无合格评审正文" not in rendered["manualReviewState"]


def test_production_attach_false_without_research_ok_remains_failure():
    candidate = _candidate("put")
    result = {
        "status": "SEMANTIC_REJECTED",
        "production_attach_allowed": False,
        "candidate_id": candidate["candidate_id"],
        "side": "put",
        "review": {
            "status": "SEMANTIC_REJECTED",
            "production_attach_allowed": False,
            "summary_cn": "语义校验失败，不是研究成功正文。",
        },
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {"last_result": result}
    manual_html = render_workbench(view)["areas"]["manualReviewState"]
    assert "本次无合格评审正文" in manual_html
    assert "研究复核完成 · 未生产附着" not in manual_html
    assert "研究回执：禁止生产附着" not in manual_html


@pytest.mark.parametrize('with_wpem',[False,True])
def test_stale_review_is_folded_when_no_current_program_questions(with_wpem):
    candidate = _candidate("put", with_wpem=with_wpem)
    candidate["review_validity"] = {"status": "STALE", "reason_cn": "snapshot changed"}
    candidate["wpem_summary"] = {"path_cn": "当前候选既有窗口摘要。" if with_wpem else "旧窗口不解释当前。", "window_status": "INSUFFICIENT_WINDOW"}
    candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": "old-snapshot",
        "summary_cn": "旧总结只属于旧输入。",
        "q1": {"summary_cn": "旧 Q1"},
        "q2": {"summary_cn": "旧 Q2"},
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    assert "旧评审已过期，仅折叠保留" in html
    assert "snapshot changed" in html
    assert "同版评审：完成" not in html.split('<details class="wpem-stale-review"')[0]
    assert "旧总结只属于旧输入。" not in html.split('<details class="wpem-stale-review"')[0]
    assert "旧总结只属于旧输入。" in html.split('<details class="wpem-stale-review"')[1]
    assert "历史 Q1 · 原冻结时点" in html
    assert "历史参考 · 原冻结资料 · 非当前判断" in html
    assert ("程序事实：当前责任位置比前窗更轻" in html) is with_wpem
    assert ("当前候选既有窗口摘要。" in html) is with_wpem
    assert "旧窗口不解释当前" not in html


def test_old_schema_without_wpem_fields_falls_back_without_badge_noise():
    html = render_workbench(_workbench([_candidate("put", with_wpem=False)]))["areas"]["candidateArea"]
    assert "BTC-PUT-SHORT" in html
    assert "wpem-panel" not in html
    assert "展开 WPEM 证据" not in html


def test_selected_side_uses_same_candidate_only_without_cross_side_ranking():
    put = _candidate("put")
    call = _candidate("call")
    call["current_questions"]["q1"]["summary_cn"] = "Call 程序事实只属于 Call。"
    view = _workbench([call, put])
    view["__test_state"] = {"selectedMapCandidateKey": "call"}
    rendered = render_workbench(view)["areas"]
    assert rendered["candidateArea"].index("Put 候选") < rendered["candidateArea"].index("Call 候选")
    assert "Call 程序事实只属于 Call" in rendered["candidateArea"]
    assert "跨侧排名" not in rendered["candidateArea"]
    assert "推荐侧" not in rendered["candidateArea"]
    assert "当前查看 · Call 保单" in rendered["manualReviewState"]


def test_single_candidate_uses_full_width_grid_class():
    html = render_workbench(_workbench([_candidate("put")]))["areas"]["candidateArea"]
    assert 'class="candidate-grid is-single"' in html


def test_version_token_contract_and_no_new_post_surface():
    version = json.loads((FRONTEND / "VERSION.json").read_text(encoding="utf-8"))
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert version["frontend_cache_token"].startswith("20261007-ui-v261-")
    assert version["local_review_revision"] == "v2.6.1-ui-information-architecture-local-not-deployed"
    assert "separate from trading quote TTL" in version["wpem_frontend_contract"]
    assert "never a newer snapshot" in version["wpem_frontend_contract"]
    assert version["wpem_request_contract_identity"]["input"] == "review_input@2.6.9"
    assert version["wpem_request_contract_identity"]["prompt"] == "evidence_synthesis_prompt@2.1-candidate"
    assert f'app.js?v={version["frontend_cache_token"]}' in index
    assert app.count('method: "POST"') == 1


def test_app_js_syntax_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run([node, "--check", str(FRONTEND / "app.js")], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
