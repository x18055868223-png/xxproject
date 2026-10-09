"""Presentation boundaries: reuse facts without upgrading identity or economics."""
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench
from test_astra_wpem_frontend import _candidate, _workbench


def render(candidate):
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    research_marker = '<details class="ia-research candidate-research-library"'
    overview = html.split(research_marker, 1)[0]
    research = html.split(research_marker, 1)[1].split('<details class="wpem-stale-review"', 1)[0].split('<details class="wpem-audit"', 1)[0]
    return html, overview, research


@pytest.mark.parametrize("path_text,tradeoff,adverse,favorable,crossings", [
    ("同一保单窗口内，价格持续远离不利责任区。", "同窗可收信用变薄，位置改善还需与当前补偿一起权衡。", 0, 3, 0),
    ("同一保单窗口内，价格持续向不利责任区靠近。", "同窗可收信用增加；补偿增强需与责任区压力一起权衡。", 3, 0, 0),
    ("同一保单窗口内，价格反复跨越责任区；末端位置不能概括这段路径。", "同窗可收信用没有明确变化。", 1, 2, 2),
])
def test_existing_path_and_tradeoff_remain_separate_and_research_explains_path(path_text, tradeoff, adverse, favorable, crossings):
    c = _candidate()
    c["current_questions"]["q1"]["window_attribution_cn"] = None
    c["current_questions"]["q2"]["window_tradeoff_cn"] = None
    c["wpem_summary"].update(path_cn=path_text, compensation_cn=tradeoff)
    ldp = c["wpem_evidence"]["same_candidate_window"]["LevelDeltaPath"]
    ldp["path"] = {"status": "PATH_OBSERVED", "adverse_steps": adverse, "favorable_steps": favorable,
                   "zone_crossings": crossings, "zone_sequence": ["outside_liability", "liability_band", "outside_liability"],
                   "price_sequence": [123456789], "point_refs": ["PRIVATE_POINT_TRAP"]}
    html, overview, research = render(c)
    questions = overview.split('aria-label="程序事实两问"', 1)[1]
    q1, q2 = questions.split("Q2 ·", 1)
    normalized_path = path_text.rstrip("。")
    normalized_tradeoff = tradeoff.rstrip("。")
    assert normalized_path in q1 and normalized_tradeoff not in q1
    assert normalized_tradeoff in q2 and normalized_path not in q2
    assert f"{adverse} 步" in research and f"{favorable} 步" in research and f"{crossings} 次" in research
    assert "责任区外侧 → 卖出腿与保护腿之间 → 责任区外侧" in research
    assert "PRIVATE_POINT_TRAP" not in html and "123456789" not in html
    assert "当前综合解释" not in overview


def test_research_does_not_reconstruct_missing_delta_path_or_fee_fields():
    c = _candidate()
    w = c["wpem_evidence"]["same_candidate_window"]
    w["LevelDeltaPath"].update({"delta": {"status": "STRICT_SAME_CANDIDATE_DELTA", "changes": {"signed_distance_to_short_pct_delta": None}, "signed_distance_to_short_pct_delta": 99999},
                               "path": {"status": "NO_PATH_DUE_TO_WINDOW_GAP", "adverse_steps": 900, "zone_crossings": 900}})
    c["wpem_evidence"]["quote_fee_deterministic_responsibility"] = {"visible_credit_btc": 7, "fee_btc": 2, "c_minus_f_btc": None}
    _, overview, research = render(c)
    assert "窗口内距卖出腿变化" not in research and "99,999" not in research
    assert "900 步" not in research and "900 次" not in research
    assert "扣持有至到期费用后的信用" not in overview


def test_existing_hold_fee_remainder_is_not_relabelled_as_entry_credit():
    c = _candidate()
    c["wpem_evidence"]["quote_fee_deterministic_responsibility"] = {"visible_credit_btc": 7, "fee_btc": 2, "c_minus_f_btc": 0.0003825}
    _, overview, _ = render(c)
    assert "扣持有至到期费用后的信用 c−f" in overview
    assert "0.0003825 BTC · 冻结费用假设下的补偿账" in overview
    assert ">5 BTC" not in overview
    assert "建仓净信用" in overview and "0.00039 BTC" in overview


def test_background_identity_and_dependency_boundaries_are_preserved_in_one_research_disclosure():
    c = _candidate()
    c["reading_guidance"] = {"notes": [{"kind": "identity", "text_cn": "这张卡依赖背景或旧版事实，不应作为当前原生准入事实。"}, {"kind": "edb_conflict", "text_cn": "价格倾向对本侧不利；同源意见不构成独立确认。"}]}
    w = c["wpem_evidence"]["same_candidate_window"]
    w["dependency_diagnostics"] = {"read_count": 30, "unique_update_group_count": 4, "reread_is_not_update": True}
    w["LevelDeltaPath"]["level"]["anchor_gex_wall_group"] = {"evidence_group": "anchor_gex_wall_dependent_group"}
    c["wpem_evidence"]["counter_catalog_gap_reassess"]["claims"] = [{"claim_cn": "SUPPORT_TRAP_NOT_FAVORABLE", "relation": "SUPPORT"}]
    _, overview, research = render(c)
    assert "SUPPORT_TRAP_NOT_FAVORABLE" not in overview + research
    assert "背景或旧版事实" in research and "同源意见不构成独立确认" in research
    assert "不构成多条独立确认" in research and "不是独立支持票数" in research
    assert "不是新增的数据源" in research
    assert "去重更新组" in research
