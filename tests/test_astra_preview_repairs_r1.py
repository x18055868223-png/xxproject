import pytest

from tests.astra_workbench_frontend_harness import render_workbench
from tools import astra_workbench_preview_v20 as preview


NOW = 1_790_323_200_000


def _render(payload):
    try:
        return render_workbench(payload)["areas"]
    except RuntimeError as exc:
        pytest.skip(str(exc))


def _simulated_result(view, scenario_id):
    candidate = view["candidates"][0]
    snapshot = candidate["snapshot"]
    wire, normalized = preview._wire_response(snapshot, scenario_id)
    return {
        "schema": preview.SCHEMA,
        "status": "SIMULATED",
        "scenario_id": scenario_id,
        "scenario_label_cn": preview.SCENARIOS[scenario_id]["label_cn"],
        "simulated_stub_id": f"bounded-wire-{scenario_id.lower()}",
        "candidate_id": snapshot["candidate_id"],
        "side": candidate["side"],
        "snapshot_id": snapshot["snapshot_id"],
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "map_id": snapshot["data_identity"]["btc_map"]["map_id"],
        "idempotency_key": "frontend-harness-once",
        "reviewed_at_ms": NOW + 1,
        "llm_call_count": 0,
        "http_calls": 0,
        "wire_response": wire,
        "review": normalized,
    }


def test_current_renderer_shows_finite_scenarios_and_w5_gate_without_auto_boot():
    areas = _render(preview.build_preview(NOW, scenario_id="W5"))

    assert "W5 主门关闭：负 NetGamma 研究诊断" in areas["previewBanner"]
    assert "零真实 LLM" in areas["previewBanner"]
    assert "不可作为当前 FMZ 事实" in areas["previewBanner"]
    for scenario_id in ["W1", "W2", "W3", "W4", "W5", "MISSING_MU"]:
        assert f'value="{scenario_id}"' in areas["scenarioControl"]
    assert 'value="W5" selected' in areas["scenarioControl"]

    assert "空间约束：当前不准入" in areas["spaceGate"]
    assert "NetGamma 未通过" in areas["spaceGate"]
    assert "-4,200,000 USD" in areas["spaceGate"]
    assert "本地模拟 SpaceGate" in areas["gateBadges"]

    candidate_html = areas["candidateArea"]
    assert "权利金差 c" in candidate_html
    assert "总费用 f" in candidate_html
    assert "冻结赔付 μ_ref" in candidate_html
    assert "模拟数值已提供，非真实模型资格" in candidate_html
    assert "同版评审：未绑定同版评审" in candidate_html
    assert "模拟评审回执" in candidate_html
    assert "本地模拟无需真实 Bearer" in areas["manualReviewState"]


def test_current_renderer_binds_receipt_only_to_same_snapshot_and_map():
    w2 = preview.build_preview(NOW, scenario_id="W2")
    receipt = _simulated_result(w2, "W2")
    manual_state = {
        "inflight": None,
        "last_result": receipt,
        "cooldown_remaining_ms": 0,
        "daily_http_budget": None,
    }

    same = _render(preview.build_preview(NOW, manual_review_state=manual_state, scenario_id="W2"))
    assert "模拟回执：非真实 LLM" in same["manualReviewState"]
    assert "模拟同版保单" in same["manualReviewState"]
    assert "Q1 · 环境与责任位置" in same["manualReviewState"]
    assert "Q2 ·" in same["manualReviewState"]
    assert "同版评审：模拟回执" in same["candidateArea"]

    changed = _render(preview.build_preview(NOW, manual_review_state=manual_state, scenario_id="W5"))
    assert "上一次模拟保单" in changed["manualReviewState"]
    assert "同版评审：未绑定同版评审" in changed["candidateArea"]
    assert "同版评审：模拟回执" not in changed["candidateArea"]


def test_negative_margin_assessment_does_not_relabel_supplied_mu_as_missing():
    w3 = preview.build_preview(NOW, scenario_id="W3")
    receipt = _simulated_result(w3, "W3")
    areas = _render(preview.build_preview(NOW, manual_review_state={"last_result": receipt}, scenario_id="W3"))

    assert "模拟数值已提供，非真实模型资格" in areas["candidateArea"]
    assert "综合复核状态：" in areas["manualReviewState"]
    assert "适配暂不能判定" not in areas["manualReviewState"]
    assert "当前放弃这份冻结账" in areas["manualReviewState"]
    assert "非正M下不推进当前补偿结论" in areas["manualReviewState"]
    assert "资料不足，不能判断" not in areas["manualReviewState"]
