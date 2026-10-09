import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402


def _candidate(*, review=None, review_gate=None):
    candidate = {
        "side": "put",
        "candidate_id": "candidate-put",
        "status": "snapshot",
        "review_gate": review_gate or {"enabled": False, "reasons": ["manual_review_service_not_configured"]},
        "snapshot": {
            "candidate_id": "candidate-put",
            "snapshot_id": "snapshot-put",
            "side": "put",
            "data_identity": {"kind": "live_public_capture"},
            "contract": {
                "side": "put",
                "short_instrument": "BTC-26SEP26-97000-P",
                "long_instrument": "BTC-26SEP26-95000-P",
                "quantity_btc": 0.1,
                "short_strike_usd": 97000,
                "long_strike_usd": 95000,
                "width_usd": 2000,
                "expiry_ms": 1790400000000,
                "remaining_to_expiry_ms": 86400000,
            },
            "market": {"reference_price_usd": 100000, "reference_source": "deribit_index", "valuation_ts_ms": 1790323200000},
            "fee": {"amount_btc": 0.0000175, "entry_fee_btc": 0.00001, "delivery_fee_btc": 0.0000075},
            "economics": {
                "premium_spread_btc": 0.0004,
                "theoretical_two_leg_fee_btc": 0.00001,
                "net_credit_after_entry_fee_btc": 0.00039,
                "net_credit_after_fee_btc": 0.0003825,
                "mu_ref_btc": None,
                "reference_margin_btc": None,
            },
            "risk": {"status": "not_configured"},
            "liability": {"payout_table": [{"settlement_usd": 97000, "payout_btc": 0.0}]},
        },
    }
    if review:
        candidate["review"] = review
    return candidate


def _workbench(*, preview, candidate, last_result=None, market_context=None):
    return {
        "schema": "astra_workbench@2.0.0",
        "updated_at_ms": 1790323200000,
        "preview": preview,
        "market_context": market_context or {},
        "space_gate": {"state": "STRUCTURE_BLOCKED", "source": "producer_native_space_gate_missing"},
        "manual_review_state": {
            "last_result": last_result or {},
            "daily_http_budget": {"http_calls_used": 0, "http_calls_limit": 5, "date_bjt": "2026-10-05"},
        },
        "candidates": [candidate],
    }


def _research_ok_review():
    return {
        "status": "RESEARCH_OK",
        "production_attach_allowed": False,
        "reviewed_snapshot_id": "snapshot-put",
        "candidate_id": "candidate-put",
        "response_protocol": "bounded_claim_ids@2.5.0",
        "applicability": "UNABLE_TO_JUDGE",
        "summary_cn": "真实研究复核正文已通过严格结构校验。",
        "q1": {"summary_cn": "真实研究 Q1 正文。"},
        "q2": {"summary_cn": "真实研究 Q2 正文。"},
    }


def _simulated_review():
    return {
        "status": "SIMULATED",
        "reviewed_snapshot_id": "snapshot-put",
        "candidate_id": "candidate-put",
        "response_protocol": "bounded_claim_ids@2.5.0",
        "applicability": "UNABLE_TO_JUDGE",
        "summary_cn": "模拟回执正文。",
        "q1": {"summary_cn": "模拟 Q1。"},
        "q2": {"summary_cn": "模拟 Q2。"},
    }


def test_replay_research_ok_retains_actual_success_without_zero_or_nonreal_llm_negation():
    review = _research_ok_review()
    view = _workbench(
        preview={"kind": "public_frozen_replay", "mode": "PUBLIC_FROZEN_REPLAY", "label": "公开冻结回放"},
        candidate=_candidate(review=review),
        last_result={**review, "side": "put", "review": review},
    )
    areas = render_workbench(view)["areas"]

    passive_html = areas["previewBanner"] + areas["candidateArea"]
    assert "冻结回放 · 非当前交易 · 查看不调用 LLM" in areas["previewBanner"]
    assert "生产 CURRENT 评审资格" in areas["previewBanner"]
    assert "冻结回放 · 非当前交易 · 零真实 LLM" not in passive_html
    assert "真实 LLM 复核" not in passive_html
    assert "非真实 LLM" not in passive_html
    assert "零真实 LLM" not in passive_html
    assert "研究复核完成 · 未生产附着" in areas["manualReviewState"]
    assert "研究回执：禁止生产附着" in areas["manualReviewState"]
    assert "真实研究复核正文已通过严格结构校验" in areas["manualReviewState"]
    assert "本次无合格评审正文" not in areas["manualReviewState"]


def test_synthetic_simulated_receipt_remains_nonreal_while_passive_labels_defer_to_receipt():
    review = _simulated_review()
    view = _workbench(
        preview={
            "kind": "synthetic_fixture",
            "mode": "SYNTHETIC_FIXTURE",
            "scenario_id": "base",
            "scenarios": [{"id": "base", "label_cn": "基准模拟"}],
        },
        candidate=_candidate(review=review),
        last_result={**review, "side": "put", "review": review},
        market_context={"status": "synthetic_fixture", "current_price_usd": 100000, "age_ms": 0},
    )
    areas = render_workbench(view)["areas"]

    assert "本地模拟 · 查看不调用 LLM · 执行关闭" in areas["previewBanner"]
    assert "评审来源以回执标识为准" in areas["previewBanner"]
    assert "查看不调用 LLM、非交易" in areas["marketContext"]
    assert "切换不调用 LLM" in areas["scenarioControl"]
    assert "本地模拟候选；来源是合成或预登记输入" in areas["candidateArea"]
    assert "评审来源以回执标识为准" in areas["candidateArea"]
    assert "不代表真实盘口、真实 LLM 或交易许可" not in areas["candidateArea"]
    assert "模拟回执：非真实 LLM" in areas["manualReviewState"]


def test_active_synthetic_demo_copy_still_marks_mock_zero_llm_and_render_makes_no_http_call():
    view = _workbench(
        preview={"kind": "synthetic_fixture", "mode": "SYNTHETIC_FIXTURE"},
        candidate=_candidate(review_gate={"enabled": True, "reasons": []}),
        market_context={"status": "synthetic_fixture", "current_price_usd": 100000, "age_ms": 0},
    )
    areas = render_workbench(view)["areas"]

    assert "点击只会调用本机模拟回执；零真实 LLM、非交易、不会产生当前事实许可" in areas["candidateArea"]
    assert "当前为本地模拟服务：按钮只演示回执流程，不调用真实 LLM，也不传生产令牌" in areas["manualReviewState"]
    assert "本地模拟 · 查看不调用 LLM · 执行关闭" in areas["previewBanner"]
    assert "零真实 LLM" not in areas["previewBanner"]
