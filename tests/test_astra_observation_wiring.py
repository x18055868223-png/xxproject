import gzip
import json
from copy import deepcopy
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import test_astra_source_consumer_v244 as source_fx
from tests import test_astra_workbench_v20 as fx
from test_astra_underwriting_review_v20 import good_answer
from test_astra_auditable_observation_pipeline import points
from tools import astra_data_engine_v23 as data_engine
from tools import astra_map_projection_v24 as map_projection
from tools import astra_underwriting_review_v20 as review_core
from tools import astra_wpem_product_review_v1 as product_review


def _research_response(snapshot):
    answer = good_answer(snapshot, research_only=True)
    catalog = review_core.build_review_input(snapshot, research_only=True)["claim_catalog"]

    def add_if_present(output_key, role, item_id):
        if any(item["id"] == item_id for item in catalog[role]):
            answer[output_key] = list(dict.fromkeys([*answer[output_key], item_id]))

    add_if_present("selected_counter_ids", "counterclaims", "counter.structure.opening_blocked")
    add_if_present("selected_reassess_condition_ids", "reassess_conditions", "reassess.structure_new_snapshot")
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(answer, ensure_ascii=False)},
        }],
        "usage": {"total_tokens": 99},
    }


def _wpem_workbench(tmp_path, *, transport=None, risk_provider=None, quote_capture=None):
    original, snapshots = fx.make_workbench(tmp_path, risk_provider=risk_provider, transport=transport)
    config = replace(original.config, wpem_enabled=True, wpem_main_loop=True)
    wb = fx.workbench.AstraWorkbench(
        config,
        discovery_capture=original.discovery_capture,
        quote_capture=quote_capture or original.quote_capture,
        risk_provider=original.risk_provider,
        email_sender=original.email_sender,
        llm_transport=transport,
        clock_ms=original.clock_ms,
    )
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    return wb, snapshots


def test_observation_post_researches_when_main_gate_is_financially_closed(tmp_path):
    calls = []
    holder = {}

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        snapshot = holder["wb"].ledger.state()["snapshots"][request["_local_snapshot_id"]]["snapshot"]
        return _research_response(snapshot)

    wb, snapshots = fx.make_workbench(tmp_path, transport=transport)
    holder["wb"] = wb
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    view = wb.refresh(now_ms=fx.T)
    item = view["candidates"][0]
    candidate_id = item["snapshot"]["candidate_id"]
    # a closed producer gate no longer closes the main review (2026-10-13)
    assert "structure_gate_not_eligible" not in item["review_gate"]["reasons"]
    assert "risk_reference_missing_or_unqualified" not in item["review_gate"]["reasons"]
    assert item["observation_review"] == {"enabled": True, "reasons": []}

    server = ThreadingHTTPServer(("127.0.0.1", 0), fx.workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({
            "candidate_id": candidate_id,
            "idempotency_key": "obs-http-1",
            "purpose": "observation",
        }).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/workbench/review",
            data=payload,
            headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert result["purpose"] == "observation"
    assert result["status"] == "RESEARCH_OK"
    assert result["production_attach_allowed"] is False
    assert result["execution_allowed"] is False
    assert result["review"]["status"] == "RESEARCH_OK"
    assert result["review"]["research_only"] is True
    assert result["review"]["production_attach_allowed"] is False
    assert result["review"]["execution_allowed"] is False
    assert calls[0]["_local_review_input"]["research_scope"]["research_only"] is True
    assert wb.cached_view()["candidates"][0]["review"]["status"] == "not_attached"


def test_observation_runtime_guards_reject_without_cross_purpose_reuse(tmp_path):
    calls = []
    holder = {}

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        snapshot = holder["wb"].ledger.state()["snapshots"][request["_local_snapshot_id"]]["snapshot"]
        return _research_response(snapshot)

    wb, snapshots = fx.make_workbench(tmp_path, transport=transport)
    holder["wb"] = wb
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    candidate_id = wb.refresh(now_ms=fx.T)["candidates"][0]["snapshot"]["candidate_id"]

    with pytest.raises(fx.workbench.WorkbenchError) as auth_error:
        wb.review_candidate(candidate_id, "obs-auth", authorization="Bearer wrong", purpose="observation")
    assert auth_error.value.status_code == 401

    missing = wb.review_candidate("missing-candidate", "obs-missing", authorization="Bearer secret", purpose="observation")
    assert missing["status"] == "rejected"
    assert missing["reason"] == "candidate_not_current"
    assert calls == []

    result = wb.review_candidate(candidate_id, "shared-key", authorization="Bearer secret", purpose="observation")
    assert result["status"] == "RESEARCH_OK"
    replay = wb.review_candidate(candidate_id, "shared-key", authorization="Bearer secret", purpose="observation")
    assert replay == result
    assert len(calls) == 1

    with pytest.raises(fx.workbench.WorkbenchError) as idem_error:
        wb.review_candidate(candidate_id, "shared-key", authorization="Bearer secret", purpose="review")
    assert idem_error.value.status_code == 409
    assert idem_error.value.code == "idempotency_key_conflict"

    with pytest.raises(fx.workbench.WorkbenchError) as cooldown_error:
        wb.review_candidate(candidate_id, "obs-cooldown", authorization="Bearer secret", purpose="observation")
    assert cooldown_error.value.status_code == 429
    assert len(calls) == 1


def test_observation_budget_and_failure_paths_leave_state_readable(tmp_path):
    calls = []
    wb, snapshots = fx.make_workbench(
        tmp_path / "budget",
        transport=lambda *args, **kwargs: calls.append(args),
    )
    ledger_path = wb.config.shared_usage_ledger
    ledger_path.unlink()
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    item = wb.refresh(now_ms=fx.T)["candidates"][0]
    assert item["observation_review"]["enabled"] is False
    assert "manual_review_service_not_configured" in item["observation_review"]["reasons"]
    rejected = wb.review_candidate(item["snapshot"]["candidate_id"], "obs-no-budget",
                                   authorization="Bearer secret", purpose="observation")
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "observation_review_unavailable"
    assert calls == []
    assert not ledger_path.exists()

    failing, failing_snapshots = fx.make_workbench(
        tmp_path / "failure",
        transport=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )
    fx.write_fmz(failing_snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    candidate_id = failing.refresh(now_ms=fx.T)["candidates"][0]["snapshot"]["candidate_id"]
    failed = failing.review_candidate(candidate_id, "obs-fails", authorization="Bearer secret", purpose="observation")
    assert failed["status"] == "failed"
    state = failing.manual_review_state(fx.T + 1)
    assert state["last_result"]["status"] == "failed"
    assert state["inflight"] is None


def test_observation_availability_survives_stale_display_quote_and_fee_gap(tmp_path):
    clock = {"now": fx.T}
    wb, snapshots = fx.make_workbench(tmp_path, clock=lambda: clock["now"])
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))
    view = wb.refresh(now_ms=fx.T)
    assert view["candidates"][0]["observation_review"]["enabled"] is True

    with wb._view_lock:
        edited = deepcopy(wb._current_view)
        edited["candidates"][0]["snapshot"]["fee"]["amount_btc"] = None
        wb._current_view = edited
    clock["now"] = fx.T + 30_000
    displayed = wb.cached_view()
    item = displayed["candidates"][0]

    assert item["current_usage"]["quote_current"] is False
    assert item["snapshot"]["fee"]["amount_btc"] is None
    assert item["review_gate"]["enabled"] is False
    assert "cached_quote_not_current" in item["review_gate"]["reasons"]
    assert item["observation_review"] == {"enabled": True, "reasons": []}


def test_manual_review_state_preserves_routing_research_receipt_explanation(tmp_path):
    wb, _snapshots = fx.make_workbench(tmp_path)
    receipt = {
        "schema": fx.workbench.SCHEMA,
        "status": "RESEARCH_OK",
        "purpose": "observation",
        "candidate_id": "candidate-routing",
        "snapshot_id": "snapshot-routing",
        "review": {
            "status": "RESEARCH_OK",
            "response_protocol": product_review.ROUTING_RESPONSE_PROTOCOL,
            "llm_explanation": {
                "q1": {"text_cn": "保留路由解释。", "evidence_refs": ["wpem.window"]},
                "q2": {"text_cn": "保留补偿解释。", "evidence_refs": ["wpem.compensation"]},
            },
        },
        "llm_call_count": 1,
    }

    wb._finish_review("routing-research", receipt, fx.T)
    state = wb.manual_review_state(fx.T + 1)

    assert state["last_result"]["status"] == "RESEARCH_OK"
    assert state["last_result"]["review"]["status"] == "RESEARCH_OK"
    assert state["last_result"]["review"]["response_protocol"] == product_review.ROUTING_RESPONSE_PROTOCOL
    assert state["last_result"]["review"]["llm_explanation"]["q1"]["text_cn"] == "保留路由解释。"


def test_manual_review_state_redacts_private_wpem_proof_and_keeps_it_out_of_the_hot_state(tmp_path):
    wb, _snapshots = fx.make_workbench(tmp_path)
    full_evidence = {
        "evidence_hash": "e" * 64,
        "composition_inputs": {"native_sources_b64": {"snapshots": "secret-source"}},
        "source_bindings": {"private": True},
        "current_cross_section": {"status": "OK"},
    }
    explanation = {"q1": {"text_cn": "解释仍应可见。", "evidence_refs": ["wpem.window"]}}
    receipt = {
        "schema": fx.workbench.SCHEMA,
        "status": "RESEARCH_OK",
        "purpose": "observation",
        "candidate_id": "candidate-private-proof",
        "snapshot_id": "snapshot-private-proof",
        "review": {
            "status": "RESEARCH_OK",
            "response_protocol": product_review.ROUTING_RESPONSE_PROTOCOL,
            "wpem_evidence": deepcopy(full_evidence),
            "llm_explanation": deepcopy(explanation),
        },
    }

    wb._finish_review("private-proof", receipt, fx.T)
    displayed = wb.manual_review_state(fx.T + 1)["last_result"]
    disk = json.loads(wb.config.state_path.read_text(encoding="utf-8"))

    public_evidence = displayed["review"]["wpem_evidence"]
    assert public_evidence["evidence_hash"] == "e" * 64
    assert "composition_inputs" not in public_evidence
    assert "source_bindings" not in public_evidence
    assert displayed["review"]["llm_explanation"] == explanation
    # Since 2026-10-14 the hot state keeps the public evidence only (the full proof stays in
    # review_state/ and wpem_evidence/): replay blobs made each receipt ~7.7 MB and the state 33 MB.
    kept = disk["manual_review"]["last_result"]["review"]["wpem_evidence"]
    assert kept["evidence_hash"] == "e" * 64 and "composition_inputs" not in kept and "source_bindings" not in kept

    unnested = fx.workbench._review_receipt_for_display({
        "status": "RESEARCH_OK",
        "response_protocol": product_review.ROUTING_RESPONSE_PROTOCOL,
        "wpem_evidence": deepcopy(full_evidence),
        "llm_explanation": deepcopy(explanation),
    })
    assert "composition_inputs" not in unnested["wpem_evidence"]
    assert "source_bindings" not in unnested["wpem_evidence"]
    assert unnested["llm_explanation"] == explanation


def test_observation_wpem_review_input_retains_cached_peer_cross_section(tmp_path):
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        payload = points()
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"total_tokens": 123},
        }

    wb, _snapshots = _wpem_workbench(tmp_path, transport=transport)
    view = wb.refresh(now_ms=fx.T)
    assert len(view["candidates"]) == 2
    selected = view["candidates"][0]
    peer = view["candidates"][1]
    selected_id = selected["snapshot"]["candidate_id"]
    peer_id = peer["snapshot"]["candidate_id"]

    result = wb.review_candidate(selected_id, "obs-wpem-peer", authorization="Bearer secret", purpose="observation")

    assert result["status"] == "RESEARCH_OK"
    assert len(calls) == 1
    proof = wb.wpem_evidence(selected["snapshot"]["snapshot_id"])
    rows = proof["current_cross_section"]["cross_section_rows"]
    assert any(row["candidate_id"] == peer_id for row in rows)
    assert calls[0]["_local_review_input"]["wpem"]["current_cross_section"]["raw_cross_section_hash"] == (
        proof["current_cross_section"]["raw_cross_section_hash"]
    )
    assert wb.wpem_evidence(peer["snapshot"]["snapshot_id"])["candidate_id"] == peer_id


def test_wpem_proof_get_gzips_large_authorized_payload_without_relaxing_plain_limit(tmp_path):
    wb, _snapshots = _wpem_workbench(tmp_path)
    item = wb.refresh(now_ms=fx.T)["candidates"][0]
    snapshot_id = item["snapshot"]["snapshot_id"]
    large_proof = wb.wpem_evidence(snapshot_id)
    large_proof["history_replay_padding"] = "x" * 8_100_000
    wb._wpem_packages[snapshot_id] = large_proof

    server = ThreadingHTTPServer(("127.0.0.1", 0), fx.workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/workbench/wpem-proof/{snapshot_id}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(Request(url, headers={"Accept-Encoding": "gzip"}), timeout=3)
        assert unauthorized.value.code == 401

        with pytest.raises(HTTPError) as plain:
            urlopen(Request(url, headers={"Authorization": "Bearer secret", "Accept-Encoding": "identity"}), timeout=3)
        assert plain.value.code == 413
        assert json.load(plain.value)["code"] == "proof_size_budget_exceeded"

        with pytest.raises(HTTPError) as disallowed:
            urlopen(Request(url, headers={"Authorization": "Bearer secret", "Accept-Encoding": "gzip;q=0, identity"}), timeout=3)
        assert disallowed.value.code == 413

        request = Request(url, headers={"Authorization": "Bearer secret", "Accept-Encoding": "gzip"})
        with urlopen(request, timeout=3) as response:
            raw = response.read()
            assert response.headers.get("Content-Encoding") == "gzip"
        decoded = json.loads(gzip.decompress(raw).decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert decoded == large_proof


def _extra_record(engine, product_id, now_ms):
    return engine.ingest(
        product_id,
        {"value": product_id, "ok": True},
        observation_end_ms=now_ms - 2_000,
        retrieved_at_ms=None,
        ingested_at_ms=now_ms,
        source_revision=product_id + ":test",
        scope=None,
    )


def _write_subset_current(tmp_path, *, extra_products=None, mutate=None):
    now_ms = fx.T
    engine = data_engine.DataEngine(tmp_path / "producer" / "data_engine")
    config = {"products": source_fx.PRODUCTS}
    source_fx._seed_background(engine, now_ms=now_ms, config=config, complete=True)
    fmz_fact = {
        "status": "current",
        "current_price_usd": 100000.0,
        "current_price_source": "deribit_index",
        "price_observed_ms": now_ms - 1_000,
    }
    records = map_projection.read_cached_sources(engine, now_ms, config)
    mapping = map_projection.build_projection(
        engine,
        now_ms=now_ms,
        fmz_fact=fmz_fact,
        source_records=records,
        config=config,
        prior_state={},
    )
    if "gex.effective.accepted.v1" in set(extra_products or ()):
        source_manifest = deepcopy(mapping["btc_map"]["source_manifest"])
        source_manifest["source_records"] = [
            record for record in source_manifest["source_records"]
            if (record.get("identity") or {}).get("product_id") != "gex.effective.accepted.v1"
        ]
        source_manifest["manifest_hash"] = data_engine._hash({
            key: value for key, value in source_manifest.items() if key != "manifest_hash"
        })
        mapping["btc_map"]["source_manifest"] = source_manifest
    extras = [_extra_record(engine, product_id, now_ms) for product_id in (extra_products or ())]
    outer_manifest = deepcopy(mapping["btc_map"]["source_manifest"])
    if extras:
        extra_manifest = engine.freeze(extras, cutoff_at_ms=now_ms)
        outer_manifest["source_records"] = list(outer_manifest["source_records"]) + list(extra_manifest["source_records"])
        outer_manifest["excluded_records"] = list(outer_manifest.get("excluded_records") or []) + list(extra_manifest.get("excluded_records") or [])
        outer_manifest["manifest_hash"] = data_engine._hash({
            key: value for key, value in outer_manifest.items() if key != "manifest_hash"
        })
    current = {
        "schema": map_projection.PRODUCER_CURRENT_SCHEMA,
        "generated_at_ms": now_ms,
        "freeze_manifest": outer_manifest,
        "btc_map": mapping["btc_map"],
        "projection_state": map_projection.persistable_state(mapping["btc_map"], {}),
        "llm_http_calls": 0,
        "trading_calls": 0,
    }
    if mutate is not None:
        mutate(current)
    path = tmp_path / "current_map.json"
    path.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path, current


@pytest.mark.parametrize("accepted_gex_already_present", [False, True])
def test_strict_subset_producer_manifest_consumes_allowed_current_fmz_extras(tmp_path, accepted_gex_already_present):
    extras = (
        "fmz.bridge.snapshots.v1",
        "fmz.spacegate.current.v1",
        "gex.effective.accepted.v1",
        "gex.netgamma.proxy.v1",
        "macro.risk_state.components.v1",
    )
    if accepted_gex_already_present:
        extras = tuple(product for product in extras if product != "gex.effective.accepted.v1")

    def duplicate_inner_record(body):
        source_manifest = deepcopy(body["btc_map"]["source_manifest"])
        source_manifest["source_records"].append(deepcopy(source_manifest["source_records"][0]))
        source_manifest["manifest_hash"] = data_engine._hash({
            key: value for key, value in source_manifest.items() if key != "manifest_hash"
        })
        body["btc_map"]["source_manifest"] = source_manifest

    current_path, current = _write_subset_current(tmp_path, extra_products=extras, mutate=duplicate_inner_record)
    qualifier = data_engine.DataEngine(tmp_path / "qualifier")

    engine = map_projection.load_producer_source_engine(current_path, now_ms=fx.T, qualifier_engine=qualifier)
    summary = engine.source_summary(fx.T)

    compat = summary["strict_subset_manifest_compatibility"]
    assert compat["mode"] == "outer_freeze_manifest_strict_superset"
    assert compat["outer_manifest_hash"] == current["freeze_manifest"]["manifest_hash"]
    assert compat["inner_manifest_hash"] == current["btc_map"]["source_manifest"]["manifest_hash"]
    assert compat["extra_product_ids"] == sorted(extras)
    assert compat["inner_record_count"] == compat["inner_unique_record_count"] + 1
    assert engine.manifest_hash == current["freeze_manifest"]["manifest_hash"]
    assert map_projection.read_cached_sources(engine, fx.T, {"products": source_fx.PRODUCTS})


def test_strict_subset_producer_manifest_rejects_tamper_and_unsupported_extras(tmp_path):
    def tamper_inner_record(body):
        source_manifest = deepcopy(body["btc_map"]["source_manifest"])
        source_manifest["source_records"][0]["content"]["values"]["tampered"] = True
        source_manifest["manifest_hash"] = data_engine._hash({
            key: value for key, value in source_manifest.items() if key != "manifest_hash"
        })
        body["btc_map"]["source_manifest"] = source_manifest

    def mutate_non_record_field(body):
        source_manifest = deepcopy(body["btc_map"]["source_manifest"])
        source_manifest["immutable"] = False
        source_manifest["manifest_hash"] = data_engine._hash({
            key: value for key, value in source_manifest.items() if key != "manifest_hash"
        })
        body["btc_map"]["source_manifest"] = source_manifest

    cases = [
        (("fmz.spacegate.current.v1",), tamper_inner_record),
        (("fmz.spacegate.current.v1",), mutate_non_record_field),
        (("btc.etf.flow.daily.v1",), None),
    ]
    for index, (extras, mutate) in enumerate(cases):
        current_path, _current = _write_subset_current(tmp_path / str(index), extra_products=extras, mutate=mutate)
        qualifier = data_engine.DataEngine(tmp_path / f"qualifier-{index}")
        with pytest.raises(map_projection.SourceConsumerError) as err:
            map_projection.load_producer_source_engine(current_path, now_ms=fx.T, qualifier_engine=qualifier)
        assert err.value.code == "PRODUCER_BTC_MAP_SOURCE_MANIFEST_MISMATCH"


def test_legacy_exact_duplicate_source_manifest_compatibility_still_works(tmp_path):
    current_path, current = source_fx._write_producer_current(tmp_path)
    source_manifest = deepcopy(current["btc_map"]["source_manifest"])
    source_manifest["source_records"].append(deepcopy(source_manifest["source_records"][0]))
    source_manifest["manifest_hash"] = data_engine._hash({
        key: value for key, value in source_manifest.items() if key != "manifest_hash"
    })
    current["btc_map"]["source_manifest"] = source_manifest
    current_path.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    qualifier = data_engine.DataEngine(tmp_path / "qualifier")

    engine = map_projection.load_producer_source_engine(current_path, now_ms=fx.T, qualifier_engine=qualifier)

    assert engine.source_summary(fx.T)["legacy_exact_duplicate_manifest"] is True
