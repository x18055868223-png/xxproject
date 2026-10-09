import json
from copy import deepcopy
from dataclasses import replace

import pytest

from tools import astra_workbench_v20 as workbench
from test_astra_auditable_observation_pipeline import points
from test_astra_workbench_v20 import T, make_workbench, native_gate, positive_risk, write_fmz


def test_retained_trace_only_source_is_forward_reachable_without_inventing_manifest():
    evidence = [{"ref": "map.etf", "lane": "current_accepted", "source_pointer": {
        "trace_candidate_id": "candidate", "trace_snapshot_id": "frozen",
        "source_refs": [{"record_id": "etf-original", "product_id": "btc.etf.flow.daily.v1",
                         "time": {"observation_end_ms": 123}, "content_hash": "original-hash"}]}}]
    records = workbench._apply_source_record_forward_maps([], evidence, enabled=True)
    assert records[0]["record_id"] == "etf-original"
    assert records[0]["time"]["observation_end_ms"] == 123
    assert records[0]["source_to_input_mapping"]["input_refs"] == ["map.etf"]
    assert records[0]["lane"] == "current_accepted"
    assert workbench._apply_source_record_forward_maps([], evidence, enabled=False) == []


def _wpem_workbench(tmp_path, *, transport=None, risk_provider=positive_risk):
    original, snapshots = make_workbench(tmp_path, risk_provider=risk_provider, transport=transport)
    wb = workbench.AstraWorkbench(
        replace(original.config, wpem_enabled=True),
        discovery_capture=original.discovery_capture,
        quote_capture=original.quote_capture,
        risk_provider=original.risk_provider,
        llm_transport=transport,
        clock_ms=original.clock_ms,
    )
    write_fmz(snapshots, native_gate())
    return wb


def _points_transport(*args, **kwargs):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(points(), ensure_ascii=False),
    }}]}


def test_current_accepted_review_exposes_readonly_consumer_receipt_and_lineage(tmp_path):
    wb = _wpem_workbench(tmp_path, transport=_points_transport)
    item = wb.refresh(now_ms=T)["candidates"][0]

    wb.review_candidate(item["snapshot"]["candidate_id"], "lineage-current", authorization="Bearer secret",
                        purpose="observation")
    item = wb.refresh(now_ms=T)["candidates"][0]
    display = item["review_display"]
    receipt = display["consumer_binding_receipt"]
    lineage = display["observation_lineage"]

    assert receipt["primary_source"] == "current_accepted"
    assert receipt["primary_owner"] == "accepted_llm_review"
    assert receipt["consumed_review_id"] == "lineage-current"
    assert receipt["consumed_review_id"] == receipt["current_binding"]["receipt_id"]
    assert receipt["consumed_at_ms"] is None
    assert receipt["consumed_at_status"] == "not_recorded"
    assert receipt["current_binding"]["selector_status"] == "CURRENT"
    assert receipt["analysis_identity"]["model"] == wb.config.model
    assert receipt["analysis_identity"]["response_protocol"] == "evidence_points@2.6.4"

    assert lineage["selected_trace_lane"] == "current_accepted"
    assert lineage["points"] == [
        {
            "receipt_id": "lineage-current",
            "lane": "current_accepted",
            "question": "q1",
            "array_index": 0,
            "text_cn": "本保单应按给出的不利方向理解已有证据；适配解释不替代程序责任账。",
            "evidence_refs": ["contract"],
        },
        {
            "receipt_id": "lineage-current",
            "lane": "current_accepted",
            "question": "q2",
            "array_index": 0,
            "text_cn": "当前报价不代表实际成交，补偿取舍应以本次冻结报价为依据。",
            "evidence_refs": ["quote"],
        },
    ]
    assert lineage["evidence"][0]["source_pointer"]["source_path"] == "contract/decision_relations.contract_geometry"
    assert lineage["evidence"][0]["source_pointer"]["canonical_snapshot_pointer"]["source_path"] == "snapshot.contract"
    assert lineage["evidence"][0]["source_pointer"]["trace_snapshot_id"] == item["snapshot"]["snapshot_id"]
    assert lineage["evidence"][0]["referenced"] is True
    assert lineage["evidence"][0]["referenced_by"] == [{
        "receipt_id": "lineage-current",
        "question": "q1",
        "array_index": 0,
    }]
    assert {point["receipt_id"] for point in lineage["points"]} == {receipt["current_binding"]["receipt_id"]}
    assert all("content" not in record for record in lineage["source_records"])
    assert all(record["lane"] == "current_snapshot_source_manifest" for record in lineage["source_records"])
    assert all("usage" not in record for record in lineage["source_records"])
    assert all("usage_qualification" in record for record in lineage["source_records"])
    assert {row["name"] for row in lineage["hash_dictionary"]} >= {
        "snapshot_id", "evidence_input_hash", "fallback_identity.hash"
    }
    serialized = json.dumps(display, ensure_ascii=False)
    for private_key in ("provider_reasoning", "raw_provider_response", "api_key", "Authorization"):
        assert private_key not in serialized


def test_rejected_attempt_keeps_program_fallback_primary_and_omits_rejected_body(tmp_path):
    wb = _wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    snapshot = item["snapshot"]
    package = wb.wpem_evidence(snapshot["snapshot_id"])
    item = deepcopy(item)
    current_record_id = item["source_manifest"]["source_records"][0]["record_id"]
    item["current_questions"]["q2"]["observable_changes_cn"] = ["观察同候选报价是否连续"]
    item["current_questions"]["q2"]["waiting_cost_cn"] = "等待可能丢失当前可见信用。"

    rejected = {
        "status": "rejected",
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "review": {
            "status": "rejected",
            "requested_model": wb.config.model,
            "response_protocol": "evidence_points@2.6.4",
            "review_validity": {
                "candidate_id": snapshot["candidate_id"],
                "snapshot_id": snapshot["snapshot_id"],
                "review_input_version": "review_input@2.6.8",
                "model": wb.config.model,
            },
            "decision_trace": {
                "binding": {
                    "candidate_id": snapshot["candidate_id"],
                    "snapshot_id": snapshot["snapshot_id"],
                    "schema": "review_input@2.6.8",
                    "response_protocol": "evidence_points@2.6.4",
                    "evidence_input_hash": "a" * 64,
                },
                "input_evidence": [{
                    "ref": "quote",
                    "label_cn": "冻结报价",
                    "group": "program_facts",
                    "lineage": {"ref": "quote", "source_path": "source_facts.quote",
                                "source_refs": [{"record_id": current_record_id}]},
                    "facts": {"credit_btc": 0.004, "executable_or_filled": False},
                }],
                "points": [{
                    "question": "q1",
                    "index": 1,
                    "text_cn": "SHOULD_NOT_APPEAR_REJECTED_BODY",
                    "evidence_refs": ["quote"],
                }],
                "audit_only": [{"field": "source_bindings", "reason_cn": "留本地审计。"}],
            },
        },
    }

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": rejected, "last_completed_ts_ms": T},
        wpem_package=package,
        model=wb.config.model,
        now_ms=T,
    )
    receipt = display["consumer_binding_receipt"]
    lineage = display["observation_lineage"]

    assert display["current"] is None
    assert display["historical"] is None
    assert receipt["primary_source"] == "program_fallback"
    assert receipt["consumed_review_id"] is None
    assert receipt["rejected_binding"]["selector_status"] == "REJECTED"
    assert receipt["analysis_identity"]["review_input_schema"] == "review_input@2.6.8"
    assert lineage["selected_trace_lane"] == "rejected_input"
    assert lineage["points"] == []
    assert lineage["evidence"][0]["lane"] == "rejected_input"
    assert lineage["evidence"][0]["referenced"] is False
    assert lineage["evidence"][0]["source_pointer"]["trace_snapshot_id"] == snapshot["snapshot_id"]
    assert all(
        record["source_to_input_mapping"]["status"] == "not_recorded"
        for record in lineage["source_records"]
    )
    assert "SHOULD_NOT_APPEAR_REJECTED_BODY" not in json.dumps(display, ensure_ascii=False)
    fields = {point["field"] for point in lineage["program_points"]}
    assert {"summary_cn", "window_attribution_cn", "waiting_cost_cn"}.issubset(fields)
    assert any(field.startswith("observable_changes_cn") for field in fields)
    assert "quote_fee_deterministic_responsibility.c_minus_f_btc" in fields


def test_stale_accepted_history_exposes_precise_selector_demotion_reason(tmp_path):
    wb = _wpem_workbench(tmp_path, transport=_points_transport)
    item = wb.refresh(now_ms=T)["candidates"][0]
    result = wb.review_candidate(item["snapshot"]["candidate_id"], "lineage-stale", authorization="Bearer secret",
                                 purpose="observation")
    stale = deepcopy(result)
    stale["snapshot_id"] = "older-snapshot"
    stale["review"]["reviewed_snapshot_id"] = "older-snapshot"

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": stale, "last_completed_ts_ms": T},
        wpem_package=wb.wpem_evidence(item["snapshot"]["snapshot_id"]),
        model=wb.config.model,
        now_ms=T,
    )

    assert display["current"] is None
    assert display["historical"] is not None
    binding = display["consumer_binding_receipt"]["historical_binding"]
    assert binding["selector_status"] == "STALE"
    assert binding["demotion_reasons"][0]["code"] == "snapshot_id_mismatch"
    assert display["observation_lineage"]["selected_trace_lane"] == "historical_accepted"
    assert "snapshot_id" in display["consumer_binding_receipt"]["historical_reason_cn"]


def test_program_fallback_summary_aliases_are_traced_and_bound():
    candidate = {"snapshot": {"snapshot_id": "frozen"},
                 "current_questions": {"q1": {}, "q2": {"window_tradeoff_cn": None}},
                 "wpem_summary": {"path_cn": "原路径边界", "compensation_cn": "原补偿边界"},
                 "wpem_evidence": {"quote_fee_deterministic_responsibility": {"c_minus_f_btc": 0.1}}}
    points = workbench._program_lineage_points(candidate)
    assert {p["text_cn"] for p in points} >= {"原路径边界", "原补偿边界"}
    original = workbench._fallback_identity(candidate)["hash"]
    changed = deepcopy(candidate)
    changed["wpem_summary"]["compensation_cn"] = "不同的可见补偿边界"
    assert workbench._fallback_identity(changed)["hash"] != original
    changed = deepcopy(candidate)
    changed["wpem_evidence"]["quote_fee_deterministic_responsibility"]["c_minus_f_btc"] = 0.2
    assert workbench._fallback_identity(changed)["hash"] != original


def test_scrub_preserves_public_nulls_but_removes_private_paths_and_usage():
    public = workbench._scrub_public_value({
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "usage": {"total_tokens": 1},
        "provider_usage": {"private": True},
        "usage_qualification": {"usage": None, "status": "USABLE"},
        "headers": {"x-api-key": "secret"},
        "x-api-key": "secret",
        "private_key": "secret",
        "source_path": "C:\\Users\\Xu\\secret.json",
        "linux_path": "/opt/private/file.json",
        "nested": [{"source_observed_ms": None}],
    })

    assert public["mu_ref_btc"] is None
    assert public["M_ref_btc"] is None
    assert public["usage_qualification"] == {"usage": None, "status": "USABLE"}
    assert public["nested"] == [{"source_observed_ms": None}]
    for key in ("usage", "provider_usage", "headers", "x-api-key", "private_key", "source_path", "linux_path"):
        assert key not in public


def test_current_lane_maps_only_exact_source_record_ids(tmp_path):
    wb = _wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    snapshot = item["snapshot"]
    record_id = item["source_manifest"]["source_records"][0]["record_id"]
    current_display = {
        "display_binding": {"receipt_id": "stable-receipt"},
        "review": {"decision_trace": {
            "binding": {
                "candidate_id": snapshot["candidate_id"],
                "snapshot_id": snapshot["snapshot_id"],
            },
            "input_evidence": [{
                "ref": "quote",
                "lineage": {
                    "ref": "quote",
                    "source_path": "source_facts.quote",
                    "source_refs": [{"record_id": record_id}, {"record_id": "not:" + record_id}],
                },
            }],
            "points": [{"question": "q2", "index": 1, "text_cn": "x", "evidence_refs": ["quote"]}],
        }},
    }

    lineage = workbench._build_observation_lineage(
        item,
        current_display=current_display,
        historical_display=None,
        rejected_binding=None,
        rejected_receipt=None,
    )

    linked = next(record for record in lineage["source_records"] if record["record_id"] == record_id)
    assert linked["source_to_input_mapping"] == {
        "status": "exact_record_id_match",
        "method": "trace.source_refs.record_id equality",
        "input_refs": ["quote"],
        "reason_cn": "当前输入 trace.source_refs 中记录了完全相同的 source record_id。",
    }
    assert lineage["evidence"][0]["source_pointer"]["canonical_snapshot_pointer"]["source_path"] == "snapshot.market"
    assert lineage["points"][0]["receipt_id"] == "stable-receipt"


def test_lineage_projection_failure_degrades_without_changing_selected_display(tmp_path, monkeypatch):
    wb = _wpem_workbench(tmp_path, transport=_points_transport)
    item = wb.refresh(now_ms=T)["candidates"][0]
    wb.review_candidate(item["snapshot"]["candidate_id"], "lineage-limited", authorization="Bearer secret",
                        purpose="observation")
    item = wb.refresh(now_ms=T)["candidates"][0]
    state = wb.manual_review_state(T)
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    def fail_lineage(*args, **kwargs):
        raise RuntimeError("bad audit projection")

    monkeypatch.setattr(workbench, "_build_observation_lineage", fail_lineage)
    display = workbench.review_display_for_candidate(
        item,
        state,
        wpem_package=package,
        model=wb.config.model,
        now_ms=T,
    )

    assert display["current"] is not None
    assert display["consumer_binding_receipt"]["primary_source"] == "current_accepted"
    assert display["observation_lineage"]["status"] == "LIMITED"
    assert display["observation_lineage"]["points"] == []
    assert "追溯投影生成失败" in display["observation_lineage"]["reason_cn"]
