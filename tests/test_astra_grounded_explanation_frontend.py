"""Grounded prose remains visible, escaped and separate from financial facts."""
import sys
import re
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench
from test_astra_wpem_frontend import _candidate, _workbench


def test_research_explanations_precede_folded_program_basis_and_preserve_boundaries():
    candidate = _candidate()
    snapshot = candidate["snapshot"]
    review = {
        "status": "RESEARCH_OK", "research_only": True,
        "production_attach_allowed": False, "candidate_id": snapshot["candidate_id"],
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "response_protocol": "bounded_claim_ids@2.6.2", "applicability": "UNABLE_TO_JUDGE",
        "deterministic_financials": {"account_basis": {
            "visible_credit_btc": 0.0027, "net_credit_after_fee_btc": 0.0023375}},
        "wpem_evidence": {"decision_asof_ms": 1790323200000, "current_cross_section": {
            "cross_section_rows": [{"snapshot_id": snapshot["snapshot_id"], "contract": {
                "side": "put", "short_strike_usd": 97000, "long_strike_usd": 95000,
                "expiry_ms": 1790400000000}}]}},
        "q1": {"summary_cn": "PROGRAM_LOCATION", "llm_explanation": {
            "text_cn": "空间余量与反向主动流需要同时看，<script>不得执行</script>。",
            "evidence_refs": ["fact_context.market_facts.flow", "wpem.window", "map.background.allocation"],
            "evidence": [{"ref": "wpem.window", "source_path": "wpem.interpretation", "provided": {
                "path_cn": "同保单实测片段：位置改善，但短窗尚不代表全程。"}},
                {"ref": "map.background.allocation", "provided": {
                    "record": {"name": "allocation", "summary_cn": "ETF慢背景保留原时点。",
                               "metrics": {"f3_usd_m": 202.8}},
                    "source_records": [{"product_id": "btc.etf.flow.daily.v1",
                                        "time": {"observation_end_ms": 1791230400000},
                                        "usage_qualification": {"status": "USABLE"}}]}}]}},
        "q2": {"summary_cn": "PROGRAM_COMPENSATION", "coverage_cn": "缺少风险参考，不能断言补偿充分。",
               "llm_explanation": {"text_cn": "信用变薄与几何改善是不同维度，保留两者的交换。",
                                   "evidence_refs": ["wpem.compensation", "quote", "fee"],
                                   "evidence": [{"ref": "fee", "provided": {"source_fee": {"fee": {
                                       "amount_btc": 0.0003625, "entry_fee_btc": None,
                                       "delivery_fee_btc": 0}}}}]}},
    }
    view = _workbench([candidate])
    view["manual_review_state"] = {"last_result": {"status": "RESEARCH_OK", "review": review,
        "candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"], "side": "put"}}
    html = render_workbench(view)["areas"]["manualReviewState"]
    assert 'aria-label="模型依据解释"' in html
    assert html.index("空间余量与反向主动流") < html.index("PROGRAM_LOCATION")
    assert '<details class="grounded-program-basis" data-disclosure-key=' in html
    assert "PROGRAM_LOCATION" in html and "PROGRAM_COMPENSATION" in html
    assert "&lt;script&gt;不得执行&lt;/script&gt;" in html and "<script>不得执行" not in html
    assert "wpem.window" in html and "本点依据" in html
    assert "同保单实测片段：位置改善" in html and "查看原始信息" in html
    assert "缺少风险参考，不能断言补偿充分" in html
    assert "研究回执：禁止生产附着" in html
    assert "解释对应保单" in html and "卖出腿 97,000 USD / 保护腿 95,000 USD" in html
    assert "资料时点 2026/9/25 16:00:00" in html
    assert "冻结权利金差 0.0027 BTC；扣冻结费用后 0.0023375 BTC" in html
    assert "冻结费用 0.0003625 BTC；建仓费用 未提供；交割费用 0 BTC" in html
    assert "ETF慢背景保留原时点" in html and "202.8 百万美元" in html
    assert "现货 BTC ETF 日净流量 · 原观察" in html and "冻结用途 可用" in html


def test_observation_action_uses_its_own_availability_without_opening_main_gate():
    candidate = _candidate()
    candidate["observation_review"] = {"enabled": True, "reasons": []}
    view = _workbench([candidate])
    html = render_workbench(view)["areas"]["candidateArea"]
    observation = re.search(r'<button[^>]+data-review-purpose="observation"[^>]*>', html).group()
    assert "disabled" not in observation
    assert "解释本单观测资料" in html
    main = re.search(r'<button[^>]+class="review-button primary"[^>]*>', html)
    if main is None:
        main = re.search(r'<button[^>]+class="primary review-button"[^>]*>', html)
    assert main is not None and "disabled" in main.group()
    candidate["observation_review"] = {"enabled": False, "reasons": ["manual_review_service_not_configured"]}
    html = render_workbench(view)["areas"]["candidateArea"]
    assert "disabled" in re.search(r'<button[^>]+data-review-purpose="observation"[^>]*>', html).group()


def test_readable_fmz_facts_are_not_described_as_globally_unavailable():
    view = _workbench()
    view["market_context"] = {"status": "background_only", "data_engine_usage": {"can_use": True}}
    rendered = render_workbench(view)["areas"]
    assert "结构未准入，已有资料可观测" in rendered["currentConclusion"]
    assert "FMZ 截面可读" in rendered["currentConclusion"]
