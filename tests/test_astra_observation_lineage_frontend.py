"""Public replay UI binds the consumed version without inventing user behaviour."""
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench
from test_astra_consumption_frontend import _accepted_display_result
from test_astra_wpem_frontend import _candidate, _workbench


def _lineage_candidate(*, accepted=True):
    candidate = _candidate("put")
    result = _accepted_display_result(candidate)
    candidate["review_display"] = {
        "current": result if accepted else None,
        "historical": None if accepted else result,
        "reason_cn": "准确冻结绑定" if accepted else "原解释输入不同",
        "consumer_binding_receipt": {
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "primary_source": "current_accepted" if accepted else "program_fallback",
            "primary_owner": "llm_review" if accepted else "deterministic_program_no_llm",
            "reason_cn": "准确冻结绑定" if accepted else "原解释输入不同",
            "consumed_review_id": "receipt-current" if accepted else None,
            "fallback_identity": None if accepted else "program-fallback-identity",
            "current_binding": result["review"]["display_binding"] if accepted else None,
            "historical_binding": None if accepted else result["review"]["display_binding"],
            "historical_reason_cn": "snapshot_changed：旧快照不解释新快照",
            "consumed_at_ms": None,
            "analysis_identity": {"requested_model": "recorded-model", "prompt_version": None},
        },
        "observation_lineage": {
            "evidence": [
                {"ref": "quote", "label_cn": "冻结报价", "referenced": True,
                 "source_paths": ["snapshot.market"], "lane": "current_accepted"},
                {"ref": "map.etf", "label_cn": "ETF背景", "referenced": False,
                 "source_paths": ["map_context.usable_background"], "lane": "current_accepted"},
            ],
            "source_records": [{"record_id": "record-1", "content_hash": "original-hash",
                                "time": {"observation_end_ms": 1790323200000}}],
            "program_points": [{"question": "q1", "text_cn": "程序责任位置原文",
                                "owner": "program_questions", "source_paths": ["snapshot.contract"]}],
            "proof_limits": ["Flow source_clock_status=NOT_RETAINED"],
            "input_omissions": [{"field": "execution_permission", "reason_cn": "audit_only"}],
            "hash_dictionary": [{"name": "evidence_input_hash", "object": "actual clean input"}],
        },
    }
    return candidate


def test_lineage_is_folded_and_separates_display_from_reading():
    html = render_workbench(_workbench([_lineage_candidate()]))["areas"]["candidateArea"]
    assert '<details class="candidate-audit-library" data-disclosure-key=' in html
    assert '<section class="observation-lineage ia-audit">' in html
    assert "审计与复盘 · 复盘这版判断" in html
    assert "主卡采用当前同版综合解释" in html
    assert "模型 Q1 点一" in html.split("复盘这版判断", 1)[0]
    assert "未记录用户是否已阅读" in html
    assert "不能用当前 Prompt 回填" in html
    assert "未被本次解释直接引用" in html and "ETF背景" in html
    assert "量价主动流 来源时钟状态=未留存" in html
    assert "NOT_RETAINED" not in html.split("审计与复盘 · 复盘这版判断", 1)[0]
    assert "事后结果不佳本身不能证明哪层失真" in html
    assert "保存这版公开复盘包" in html


def test_fallback_lineage_exposes_program_owner_and_history_reason():
    html = render_workbench(_workbench([_lineage_candidate(accepted=False)]))["areas"]["candidateArea"]
    assert "主卡采用程序事实底座" in html
    assert "程序 Q1/Q2 如何连接冻结事实" in html
    assert "程序责任位置原文" in html
    assert "snapshot_changed：旧快照不解释新快照" in html


def test_source_clock_null_is_not_rendered_as_epoch_time():
    candidate = _lineage_candidate()
    candidate["review_display"]["observation_lineage"]["source_records"][0]["time"]["observation_end_ms"] = None
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    assert "原观察 未留存" in html
    assert "1970/" not in html


def test_historical_prompt_uses_historical_snapshot_identity_and_labels_program_primary():
    candidate = _lineage_candidate(accepted=False)
    candidate["review_display"]["consumer_binding_receipt"]["historical_binding"]["snapshot_id"] = "archive-snapshot"
    view = _workbench([candidate])
    view["observation_replay"] = {"original_analysis_identity": {
        "historical_snapshot_id": "archive-snapshot", "prompt": "archive-prompt-2.1"}}
    html = render_workbench(view)["areas"]["candidateArea"]
    assert "当前主卡为程序事实底座，不对应 LLM 请求" in html
    assert "历史参考解释的原模型" in html
    assert "原 Prompt：archive-prompt-2.1" in html
    assert "主卡 LLM 输入：无（程序底座）" in html


def test_point_index_uses_original_array_position_and_trace_one_based_index():
    candidate = _lineage_candidate()
    review = candidate["review_display"]["current"]["review"]
    # A missing text is resolved from the same point, not its preceding point.
    review["q1"]["llm_explanation"]["points"] = [
        {},
        {"evidence_refs": ["fee"]},
    ]
    review["decision_trace"]["points"] = [
        {"question": "q1", "index": 1, "text_cn": "WRONG_PREVIOUS_POINT"},
        {"question": "q1", "index": 2, "text_cn": "CORRECT_SECOND_POINT"},
    ]
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    assert "CORRECT_SECOND_POINT" in html
    assert "WRONG_PREVIOUS_POINT" not in html
    assert 'data-point-array-index="1"' in html
    second_point = html.split('data-point-array-index="1"', 1)[1].split('data-point-question="q2"', 1)[0]
    assert '"array_index_0based": 1' in second_point.replace("&quot;", '"')
    assert '"trace_index_1based": 2' in second_point.replace("&quot;", '"')


def test_capture_retains_nulls_binding_and_removes_private_fields_recursively():
    candidate = _lineage_candidate()
    candidate["snapshot"]["risk"]["mu_ref_btc"] = None
    candidate["current_questions"]["nested_private_test"] = [
        {"reasoning_content": "PRIVATE_REASONING",
         "provider_response": {"text": "PRIVATE_PROVIDER"},
         "client_secret": "PRIVATE_SECRET",
         "x-api-key": "PRIVATE_KEY",
         "private_key": "PRIVATE_RSA",
         "usage_metadata": {"private_cost": 88},
         "server_file": "C:/private/server.json",
         "source_path": "snapshot.market",
         "usage_qualification": {"status": "PARTIAL"}}
    ]
    output = render_workbench(_workbench([candidate]), capture=True)
    capture = output["captures"][0]
    text = json.dumps(capture, ensure_ascii=False)
    assert "PRIVATE_" not in text
    assert "private_cost" not in text and "C:/private" not in text
    assert "snapshot.market" in text and "usage_qualification" in text
    assert capture["original_consumed_at_ms"] is None
    assert capture["rendered_at_ms"] is not None
    assert capture["user_read_or_traded"] == "not_recorded"
    assert capture["consumer_binding_receipt"]["consumed_review_id"] == "receipt-current"
    frozen = capture["public_view"]["candidates"][0]
    assert frozen["snapshot"]["risk"]["mu_ref_btc"] is None
    assert frozen["review_gate"]["enabled"] is False
    assert capture["execution_allowed"] is False
    assert capture["visible_primary_text"] == "test-rendered-primary"
