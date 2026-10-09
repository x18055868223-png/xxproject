from copy import deepcopy
from dataclasses import replace
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_product_review_v1 as product
from test_astra_workbench_v20 import (
    T,
    fake_quote_capture,
    make_workbench,
    native_gate,
    positive_risk,
    write_fmz,
)


def wpem_workbench(tmp_path, *, clock=lambda: T):
    original, snapshots = make_workbench(
        tmp_path,
        risk_provider=positive_risk,
        transport=lambda *args, **kwargs: pytest.fail("LLM transport must not be called"),
        clock=clock,
    )
    wb = workbench.AstraWorkbench(
        replace(original.config, wpem_enabled=True),
        discovery_capture=original.discovery_capture,
        quote_capture=original.quote_capture,
        risk_provider=original.risk_provider,
        llm_transport=original.llm_transport,
        clock_ms=original.clock_ms,
    )
    write_fmz(snapshots, native_gate(ts=clock() - 1000), now=clock())
    return wb, snapshots


def evidence_refs(review_input):
    refs = [
        item["ref"]
        for item in (review_input.get("evidence_input") or {}).get("evidence") or []
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    ]
    return refs[:1] or ["data.contract"]


def accepted_receipt(wb, item, *, key="accepted-current", reviewed_at=T + 1, private=False):
    snapshot = item["snapshot"]
    package = wb.wpem_evidence(snapshot["snapshot_id"])
    review_input = product.build_review_input(
        snapshot,
        package,
        research_only=True,
        model=wb.config.model,
    )
    refs = evidence_refs(review_input)
    explanation = {
        "q1": {
            "points": [{"text_cn": "Q1 accepted point", "evidence_refs": refs}],
            "text_cn": "Q1 accepted point",
            "evidence_refs": refs,
        },
        "q2": {
            "points": [{"text_cn": "Q2 accepted point", "evidence_refs": refs}],
            "text_cn": "Q2 accepted point",
            "evidence_refs": refs,
        },
    }
    body = {
        "schema": product.research_review_schema_for_input(review_input),
        "status": "RESEARCH_OK",
        "research_only": True,
        "production_attach_allowed": False,
        "execution_allowed": False,
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "packet_hash": review_input["packet_hash"],
        "projection_hash": review_input["projection_hash"],
        "input_scope": "test",
        "requested_model": wb.config.model,
        "response_protocol": product.response_protocol_for_input(review_input),
        "claim_catalog_hash": review_input["claim_catalog_hash"],
        "applicability": "UNABLE_TO_JUDGE",
        "selected_claim_ids": [],
        "selected_counter_ids": [],
        "selected_gap_ids": [],
        "selected_reassess_condition_ids": [],
        "supports": [],
        "counters": [],
        "q1": {"llm_explanation": deepcopy(explanation["q1"])},
        "q2": {"llm_explanation": deepcopy(explanation["q2"])},
        "gaps_cn": [],
        "reassess_if_cn": [],
        "summary_cn": "accepted summary",
        "deterministic_financials": {},
        "http_calls": 0,
        "wpem_evidence": deepcopy(package),
        "review_validity": product.review_binding(
            snapshot,
            package,
            review_input,
            model=wb.config.model,
        ),
        "llm_explanation": deepcopy(explanation),
        "decision_trace": product.build_decision_trace(review_input, {"llm_explanation": explanation}),
        "selection_origin": "test_fixture",
    }
    receipt = {
        "schema": workbench.SCHEMA,
        "status": "RESEARCH_OK",
        "purpose": "observation",
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "idempotency_key": key,
        "review": body,
        "llm_call_count": 0,
    }
    if private:
        receipt["raw_response"] = {"secret": True}
        receipt["review"]["provider_response"] = {"secret": True}
        receipt["review"]["llm_explanation"]["q1"]["points"][0]["reasoning_content"] = "private reasoning"
    return receipt


def rejected_receipt(item, *, key="rejected-current"):
    snapshot = item["snapshot"]
    return {
        "schema": workbench.SCHEMA,
        "status": "rejected",
        "purpose": "observation",
        "reason": "semantic_rejected",
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "idempotency_key": key,
        "review": {"status": "rejected", "body_cn": "failure body must not display"},
        "llm_call_count": 0,
    }


def assert_key_absent(value, key):
    if isinstance(value, dict):
        assert key not in value
        for item in value.values():
            assert_key_absent(item, key)
    elif isinstance(value, list):
        for item in value:
            assert_key_absent(item, key)


def api_workbench(wb):
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench", timeout=3) as response:
            return json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_same_snapshot_accepted_becomes_current_in_cached_and_api_response(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    wb._finish_review("accepted-current", receipt, T + 1)

    displayed = wb.cached_view()["candidates"][0]
    current = displayed["review_display"]["current"]

    assert displayed["review"]["status"] == "not_attached"
    assert current["status"] == "RESEARCH_OK"
    assert current["production_attach_allowed"] is False
    assert current["execution_allowed"] is False
    assert current["display_binding"]["same_input"] is True
    assert current["display_binding"]["candidate_id"] == item["snapshot"]["candidate_id"]
    assert current["display_binding"]["snapshot_id"] == item["snapshot"]["snapshot_id"]
    assert current["display_binding"]["evidence_input_hash"]
    assert displayed["review_display"]["historical"] is None

    api_item = api_workbench(wb)["candidates"][0]
    assert api_item["review_display"]["current"]["display_binding"]["same_input"] is True


def test_changed_snapshot_and_source_hash_demote_accepted_to_historical(tmp_path):
    current_time = [T]
    wb, snapshots = wpem_workbench(tmp_path, clock=lambda: current_time[0])
    first = wb.refresh(now_ms=current_time[0])["candidates"][0]
    receipt = accepted_receipt(wb, first)
    wb._finish_review("accepted-old", receipt, T + 1)

    package = wb.wpem_evidence(first["snapshot"]["snapshot_id"])
    changed_package = deepcopy(package)
    changed_package["evidence_hash"] = "f" * 64
    direct = workbench.review_display_for_candidate(
        first,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=changed_package,
        model=wb.config.model,
        now_ms=T,
    )
    assert direct["current"] is None
    assert direct["historical"]["display_binding"]["same_input"] is False

    current_time[0] = T + 1000
    write_fmz(snapshots, native_gate(ts=current_time[0] - 1000), now=current_time[0])
    changed = wb.refresh(now_ms=current_time[0])["candidates"][0]

    assert changed["snapshot"]["candidate_id"] == first["snapshot"]["candidate_id"]
    assert changed["snapshot"]["snapshot_id"] != first["snapshot"]["snapshot_id"]
    assert changed["review_display"]["current"] is None
    assert changed["review_display"]["historical"]["snapshot_id"] == first["snapshot"]["snapshot_id"]
    assert changed["review_display"]["historical"]["display_binding"]["same_input"] is False


def test_same_input_current_survives_review_latency_and_cooldown_state(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    for latency_ms in (10_000, 180_000):
        display = workbench.review_display_for_candidate(
            item,
            {"last_result": receipt, "last_completed_ts_ms": T + 1, "cooldown_remaining_ms": 599_000},
            wpem_package=package,
            model=wb.config.model,
            now_ms=T + latency_ms,
        )

        assert display["current"]["display_binding"]["same_input"] is True
        assert display["historical"] is None


def test_future_quote_clock_is_not_current(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=package,
        model=wb.config.model,
        now_ms=T - 1,
    )

    assert display["current"] is None
    assert display["historical"]["display_binding"]["same_input"] is False


def test_contract_expiry_demotes_same_input_review_to_history(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])
    expiry_ms = item["snapshot"]["contract"]["expiry_ms"]

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=package,
        model=wb.config.model,
        now_ms=expiry_ms + 1,
    )

    assert display["current"] is None
    assert display["historical"]["display_binding"]["same_input"] is False


def test_duplicate_receipt_is_bound_once(tmp_path, monkeypatch):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])
    calls = []
    original = workbench.wpem_review.build_review_input

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(workbench.wpem_review, "build_review_input", counted)
    display = workbench.review_display_for_candidate(
        item,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=package,
        model=wb.config.model,
        now_ms=T + 10_000,
        accepted_history=[{"result": deepcopy(receipt), "updated_at_ms": T + 1}],
    )

    assert display["current"]["display_binding"]["same_input"] is True
    assert len(calls) == 1


def test_current_failure_falls_back_without_showing_rejected_body_and_keeps_old_history(tmp_path):
    current_time = [T]
    wb, snapshots = wpem_workbench(tmp_path, clock=lambda: current_time[0])
    first = wb.refresh(now_ms=current_time[0])["candidates"][0]
    old_receipt = accepted_receipt(wb, first, key="accepted-old")
    wb._finish_review("accepted-old", old_receipt, T + 1)

    current_time[0] = T + 1000
    write_fmz(snapshots, native_gate(ts=current_time[0] - 1000), now=current_time[0])
    second = wb.refresh(now_ms=current_time[0])["candidates"][0]
    wb._finish_review("failed-new", rejected_receipt(second), current_time[0] + 1)

    displayed = wb.cached_view()["candidates"][0]["review_display"]

    assert displayed["current"] is None
    assert displayed["historical"]["snapshot_id"] == first["snapshot"]["snapshot_id"]
    assert "失败正文" not in json.dumps(displayed, ensure_ascii=False)
    assert "failure body" not in json.dumps(displayed, ensure_ascii=False)
    assert "程序事实" in displayed["reason_cn"] or "历史参考" in displayed["reason_cn"]


def test_nested_reasoning_and_raw_provider_fields_are_not_public(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item, private=True)
    wb._finish_review("private-current", receipt, T + 1)

    state = wb.manual_review_state(T)
    displayed = wb.cached_view()["candidates"][0]["review_display"]["current"]

    for public in (state["last_result"], displayed):
        assert_key_absent(public, "raw_response")
        assert_key_absent(public, "provider_response")
        assert_key_absent(public, "reasoning_content")


def test_trace_evidence_input_hash_tamper_demotes_without_package_change(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    receipt["review"]["decision_trace"]["binding"]["evidence_input_hash"] = "tampered-input-hash"
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=package,
        model=wb.config.model,
        now_ms=T,
    )

    assert display["current"] is None
    assert display["historical"]["display_binding"]["same_input"] is False
    assert display["historical"]["display_binding"]["evidence_input_hash"] == "tampered-input-hash"


def test_outer_rejected_inner_ok_is_not_accepted(tmp_path):
    wb, _snapshots = wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=T)["candidates"][0]
    receipt = accepted_receipt(wb, item)
    receipt["status"] = "rejected"
    receipt["reason"] = "outer_rejected"
    package = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    display = workbench.review_display_for_candidate(
        item,
        {"last_result": receipt, "last_completed_ts_ms": T + 1},
        wpem_package=package,
        model=wb.config.model,
        now_ms=T,
    )

    assert display["current"] is None
    assert display["historical"] is None
    assert "程序事实" in display["reason_cn"]
